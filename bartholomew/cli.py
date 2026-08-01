"""
Bartholomew Admin CLI
Provides admin commands for embeddings management and system operations
"""

import sys

try:
    import typer
    from rich.console import Console
    from rich.table import Table
except ImportError:
    print("Error: CLI dependencies not installed.")
    print("Install with: pip install typer rich")
    sys.exit(1)


app = typer.Typer(help="Bartholomew Admin CLI")
console = Console()
embeddings_app = typer.Typer(help="Embeddings management commands")
brake_app = typer.Typer(help="Parking brake safety controls")
app.add_typer(embeddings_app, name="embeddings")
app.add_typer(brake_app, name="brake")


@embeddings_app.command("stats")
def embeddings_stats(
    db: str = typer.Option("data/bartholomew.db", help="Path to database file"),
):
    """Show embeddings statistics and configuration"""
    import os
    import sqlite3

    from bartholomew.kernel import db_ctx
    from bartholomew.kernel.embedding_engine import get_embedding_engine
    from bartholomew.kernel.vector_store import VectorStore

    console.print("\n[bold]Embeddings Statistics[/bold]")
    console.print(f"Database: {db}\n")

    # Check if embeddings are enabled
    enabled = os.getenv("BARTHO_EMBED_ENABLED") == "1"
    console.print(
        f"Enabled: {'✓' if enabled else '✗'} "
        f"(BARTHO_EMBED_ENABLED={'1' if enabled else 'not set'})",
    )

    if not enabled:
        console.print("\n[yellow]Enable with: BARTHO_EMBED_ENABLED=1[/yellow]\n")
        return

    # Get engine config
    try:
        engine = get_embedding_engine()
        cfg = engine.config
        console.print(f"Provider: {cfg.provider}")
        console.print(f"Model: {cfg.model}")
        console.print(f"Dimension: {cfg.dim}")

        # Check fallback status
        if hasattr(engine.provider, "fallback"):
            fallback_status = "yes" if engine.provider.fallback else "no"
            console.print(f"Fallback mode: {fallback_status}")
    except Exception as e:
        console.print(f"[red]Error loading engine: {e}[/red]")
        return

    # Check VSS availability
    try:
        vec_store = VectorStore(db)
        vss_status = "✓ enabled" if vec_store.vss_available else "✗ disabled"
        console.print(f"SQLite VSS: {vss_status}")
    except Exception as e:
        console.print(f"[red]Error loading vector store: {e}[/red]")
        return

    # Database stats
    if not os.path.exists(db):
        console.print(f"\n[yellow]Database not found: {db}[/yellow]\n")
        return

    try:
        with db_ctx.connect(db) as conn:
            db_ctx.set_wal_pragmas(conn)
            conn.row_factory = sqlite3.Row

            # Total count
            cursor = conn.execute("SELECT COUNT(*) FROM memory_embeddings")
            total = cursor.fetchone()[0]
            console.print(f"\n[bold]Total embeddings:[/bold] {total}")

            if total > 0:
                # Distribution by (provider, model, dim)
                cursor = conn.execute(
                    """
                    SELECT provider, model, dim, COUNT(*) as count
                    FROM memory_embeddings
                    GROUP BY provider, model, dim
                    ORDER BY count DESC
                    LIMIT 5
                """,
                )

                table = Table(title="Top Configurations")
                table.add_column("Provider", style="cyan")
                table.add_column("Model", style="magenta")
                table.add_column("Dim", style="green")
                table.add_column("Count", style="yellow")

                for row in cursor:
                    table.add_row(row["provider"], row["model"], str(row["dim"]), str(row["count"]))

                console.print(table)

                # Source distribution
                cursor = conn.execute(
                    """
                    SELECT source, COUNT(*) as count
                    FROM memory_embeddings
                    GROUP BY source
                """,
                )

                console.print("\n[bold]By source:[/bold]")
                for row in cursor:
                    console.print(f"  {row['source']}: {row['count']}")

            console.print()
    except Exception as e:
        console.print(f"[red]Database error: {e}[/red]\n")


@embeddings_app.command("rebuild-vss")
def embeddings_rebuild_vss(
    db: str = typer.Option("data/bartholomew.db", help="Path to database file"),
):
    """Rebuild SQLite VSS virtual table and triggers"""
    import os

    from bartholomew.kernel import db_ctx

    console.print(f"\n[bold]Rebuilding VSS for {db}[/bold]\n")

    if not os.path.exists(db):
        console.print(f"[red]Database not found: {db}[/red]\n")
        raise typer.Exit(1)

    try:
        with db_ctx.connect(db) as conn:
            db_ctx.set_wal_pragmas(conn)

            # Check if VSS extension available
            try:
                conn.enable_load_extension(True)
                conn.load_extension("vss0")
                console.print("✓ sqlite-vss extension loaded")
            except Exception as e:
                console.print(f"[red]✗ VSS extension not available: {e}[/red]")
                console.print("\nVSS is optional. Install from:")
                console.print("  https://github.com/asg017/sqlite-vss\n")
                raise typer.Exit(1) from None

            # Drop existing VSS table and triggers
            console.print("Dropping existing VSS table and triggers...")
            conn.execute("DROP TABLE IF EXISTS memory_embeddings_vss")
            conn.execute("DROP TRIGGER IF EXISTS trg_mememb_insert")
            conn.execute("DROP TRIGGER IF EXISTS trg_mememb_update")
            conn.execute("DROP TRIGGER IF EXISTS trg_mememb_delete")
            conn.commit()
            console.print("✓ Dropped")

            # Create VSS virtual table (hardcoded to 384 for Phase 2d)
            console.print("Creating VSS virtual table...")
            conn.execute(
                """
                CREATE VIRTUAL TABLE memory_embeddings_vss
                USING vss0(vec(384))
            """,
            )
            console.print("✓ Created")

            # Create triggers
            console.print("Creating triggers...")
            conn.execute(
                """
                CREATE TRIGGER trg_mememb_insert
                AFTER INSERT ON memory_embeddings
                WHEN NEW.dim = 384
                BEGIN
                    INSERT INTO memory_embeddings_vss(rowid, vec)
                    VALUES (NEW.embedding_id, NEW.vec);
                END
            """,
            )

            conn.execute(
                """
                CREATE TRIGGER trg_mememb_update
                AFTER UPDATE OF vec, dim, model, provider, source
                ON memory_embeddings
                BEGIN
                    DELETE FROM memory_embeddings_vss
                    WHERE rowid = NEW.embedding_id;

                    INSERT INTO memory_embeddings_vss(rowid, vec)
                    SELECT NEW.embedding_id, NEW.vec
                    WHERE NEW.dim = 384;
                END
            """,
            )

            conn.execute(
                """
                CREATE TRIGGER trg_mememb_delete
                AFTER DELETE ON memory_embeddings
                BEGIN
                    DELETE FROM memory_embeddings_vss
                    WHERE rowid = OLD.embedding_id;
                END
            """,
            )
            conn.commit()
            console.print("✓ Triggers created")

            # Populate with existing 384-dim vectors
            console.print("Populating VSS table...")
            cursor = conn.execute(
                """
                INSERT INTO memory_embeddings_vss(rowid, vec)
                SELECT embedding_id, vec
                FROM memory_embeddings
                WHERE dim = 384
            """,
            )
            count = cursor.rowcount
            conn.commit()
            console.print(f"✓ Inserted {count} vectors")

            console.print("\n[green]VSS rebuild complete![/green]\n")
    except Exception as e:
        console.print(f"\n[red]Error: {e}[/red]\n")
        raise typer.Exit(1) from e


def _resolve_brake_db_path(db: str | None) -> str:
    """
    Resolve the brake commands' effective database path. Phase B, stage B6:
    previously defaulted to "data/bartholomew.db" -- a different filename
    than the live daemon's own default ("data/barth.db", via
    _default_db_path()/BARTH_DB_PATH; see docs/B0_BASELINE_REPORT.md
    section 1). That divergence meant a bare `bartholomew brake on` could
    silently engage a brake the running daemon never reads, making the
    emergency stop a no-op against a live process -- exactly the class of
    problem this stage exists to close. Now resolves the same way the
    daemon does unless --db is passed explicitly.
    """
    if db is not None:
        return db
    from bartholomew.kernel.daemon import _default_db_path

    return _default_db_path()


def _report_live_daemon(db_path: str) -> None:
    """Informational only -- see docs/B6_IMPLEMENTATION.md's explanation of
    why brake on/off are not fenced by the process lock: engage()/
    disengage() must keep working as an emergency stop against a live
    daemon, and GovernanceBrakeStore's transitions are already safe under
    concurrent access (atomic, revision-tracked)."""
    from bartholomew.kernel.process_lock import ProcessLock

    lock = ProcessLock(f"{db_path}.lock")
    if lock.is_held_by_other():
        pid = lock.owner_pid()
        console.print(
            f"[cyan]A live daemon appears to be running{f' (pid {pid})' if pid else ''} "
            f"against this database -- it will observe this change on its next check.[/cyan]",
        )
    else:
        console.print(
            "[dim]No live daemon detected against this database -- this sets the "
            "persisted state a daemon will read when one next starts.[/dim]",
        )


@brake_app.command("on")
def brake_on(
    scope: list[str] = typer.Option(
        None,
        "--scope",
        help="Scopes to block (global, skills, sight, voice, scheduler)",
    ),
    db: str = typer.Option(
        default=None,
        help="Path to database file (defaults to the same resolution the live daemon uses)",
    ),
):
    """
    Engage parking brake (block specified scopes).

    Phase B, stage B6: this now UNIONS the given scopes into whatever is
    already engaged, rather than replacing them -- running `brake on
    --scope skills` after `brake on --scope global` keeps "global" blocked
    too, instead of silently narrowing to only "skills". See
    docs/B3_IMPLEMENTATION.md for why the previous replace-based behavior
    was a real fail-closed-governance defect.
    """
    from bartholomew.kernel.governance.brake_store import GovernanceBrakeStore

    db_path = _resolve_brake_db_path(db)
    scopes = scope if scope else ["global"]

    store = GovernanceBrakeStore(db_path)
    state = store.engage(*scopes, actor="cli")

    console.print(f"\nDatabase: {db_path}")
    console.print(
        f"[yellow]⚠ Parking brake ENGAGED[/yellow] - Scopes: {', '.join(sorted(state.scopes))}\n",
    )
    _report_live_daemon(db_path)


@brake_app.command("off")
def brake_off(
    db: str = typer.Option(
        default=None,
        help="Path to database file (defaults to the same resolution the live daemon uses)",
    ),
):
    """Disengage parking brake (allow all components). Running this command
    is itself the explicit confirmation GovernanceBrakeStore.disengage()
    requires."""
    from bartholomew.kernel.governance.brake_store import GovernanceBrakeStore

    db_path = _resolve_brake_db_path(db)
    store = GovernanceBrakeStore(db_path)
    store.disengage(confirm=True, actor="cli")

    console.print(f"\nDatabase: {db_path}")
    console.print("[green]✓ Parking brake DISENGAGED[/green] - All components allowed\n")
    _report_live_daemon(db_path)


@brake_app.command("status")
def brake_status(
    db: str = typer.Option(
        default=None,
        help="Path to database file (defaults to the same resolution the live daemon uses)",
    ),
):
    """Show parking brake status"""
    from bartholomew.kernel.governance.brake_store import GovernanceBrakeStore

    db_path = _resolve_brake_db_path(db)
    store = GovernanceBrakeStore(db_path)
    state = store.current_state()

    console.print("\n[bold]Parking Brake Status[/bold]")
    console.print(f"Database: {db_path}\n")

    if state.engaged:
        console.print("[yellow]Status: ENGAGED (blocking)[/yellow]")
        console.print(f"Scopes: {', '.join(sorted(state.scopes))}\n")
    else:
        console.print("[green]Status: DISENGAGED (allowing all)[/green]\n")

    _report_live_daemon(db_path)


def main():
    """Entry point for CLI"""
    app()


if __name__ == "__main__":
    main()
