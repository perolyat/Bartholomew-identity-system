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
        with sqlite3.connect(db) as conn:
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
    import sqlite3

    console.print(f"\n[bold]Rebuilding VSS for {db}[/bold]\n")

    if not os.path.exists(db):
        console.print(f"[red]Database not found: {db}[/red]\n")
        raise typer.Exit(1)

    try:
        with sqlite3.connect(db) as conn:
            conn.execute("PRAGMA foreign_keys = ON")

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


@brake_app.command("on")
def brake_on(
    scope: list[str] = typer.Option(
        default=None,
        param_decls=["--scope"],
        help="Scopes to block (global, skills, sight, voice, scheduler)",
    ),
    db: str = typer.Option(
        default="data/bartholomew.db",
        help="Path to database file",
    ),
):
    """Engage parking brake (block specified scopes)"""
    from bartholomew.orchestrator.safety.parking_brake import BrakeStorage, ParkingBrake

    # Default to global if no scopes specified
    scopes = scope if scope else ["global"]

    storage = BrakeStorage(db)
    brake = ParkingBrake(storage)
    brake.engage(*scopes)

    console.print(
        f"\n[yellow]⚠ Parking brake ENGAGED[/yellow] - Scopes: {', '.join(sorted(scopes))}\n",
    )


@brake_app.command("off")
def brake_off(
    db: str = typer.Option("data/bartholomew.db", help="Path to database file"),
):
    """Disengage parking brake (allow all components)"""
    from bartholomew.orchestrator.safety.parking_brake import BrakeStorage, ParkingBrake

    storage = BrakeStorage(db)
    brake = ParkingBrake(storage)
    brake.disengage()

    console.print("\n[green]✓ Parking brake DISENGAGED[/green] - All components allowed\n")


@brake_app.command("status")
def brake_status(
    db: str = typer.Option("data/bartholomew.db", help="Path to database file"),
):
    """Show parking brake status"""
    from bartholomew.orchestrator.safety.parking_brake import BrakeStorage, ParkingBrake

    storage = BrakeStorage(db)
    brake = ParkingBrake(storage)
    state = brake.state()

    console.print("\n[bold]Parking Brake Status[/bold]")
    console.print(f"Database: {db}\n")

    if state.engaged:
        console.print("[yellow]Status: ENGAGED (blocking)[/yellow]")
        console.print(f"Scopes: {', '.join(sorted(state.scopes))}\n")
    else:
        console.print("[green]Status: DISENGAGED (allowing all)[/green]\n")


def main():
    """Entry point for CLI"""
    app()


if __name__ == "__main__":
    main()
