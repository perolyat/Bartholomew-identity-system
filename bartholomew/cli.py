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

    from bartholomew.kernel.process_lock import ProcessLock, ProcessLockHeldError

    console.print(f"\n[bold]Rebuilding VSS for {db}[/bold]\n")

    if not os.path.exists(db):
        console.print(f"[red]Database not found: {db}[/red]\n")
        raise typer.Exit(1)

    # Phase B stage B6: unlike brake on/off/status (which are designed to
    # control a *running* daemon and are protected by GovernanceStore's own
    # write fence + revision guarding instead), this operation rewrites
    # memory_embeddings_vss and its triggers wholesale with no revision
    # guarding of its own -- it assumes exclusive access to the database
    # file, so it takes the process lock and refuses outright if the daemon
    # (or another maintenance command) already holds it, rather than risking
    # a conflicting concurrent write.
    lock = ProcessLock(db)
    try:
        lock.acquire()
    except ProcessLockHeldError as e:
        console.print(f"[red]{e}[/red]\n")
        raise typer.Exit(1) from e

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
    finally:
        lock.release()


_CLI_BRAKE_REASON_PREFIX = "CLI"


@brake_app.command("on")
def brake_on(
    scope: list[str] = typer.Option(
        None,
        "--scope",
        help="Scopes to block (global, skills, sight, voice, scheduler, training)",
    ),
    db: str = typer.Option(
        default="data/bartholomew.db",
        help="Path to database file",
    ),
):
    """Engage parking brake (block specified scopes)"""
    from bartholomew.orchestrator.safety.governance_store import (
        GovernanceStore,
        WriteFenceClosedError,
    )

    # Default to global if no scopes specified
    scopes = scope if scope else ["global"]

    store = GovernanceStore(db)
    try:
        store.engage(
            *scopes,
            reason=f"{_CLI_BRAKE_REASON_PREFIX}: brake on --scope {','.join(sorted(scopes))}",
            actor="cli",
        )
    except WriteFenceClosedError as e:
        console.print(f"\n[red]✗ Could not engage: {e}[/red]\n")
        raise typer.Exit(1) from e

    console.print(
        f"\n[yellow]⚠ Parking brake ENGAGED[/yellow] - Scopes: {', '.join(sorted(scopes))}\n",
    )


@brake_app.command("off")
def brake_off(
    db: str = typer.Option("data/bartholomew.db", help="Path to database file"),
):
    """Disengage parking brake (allow all components)"""
    from bartholomew.orchestrator.safety.governance_store import (
        GovernanceStore,
        StaleGovernanceWriteError,
        WriteFenceClosedError,
    )

    store = GovernanceStore(db)
    try:
        store.disengage(reason=f"{_CLI_BRAKE_REASON_PREFIX}: brake off", actor="cli")
    except WriteFenceClosedError as e:
        console.print(f"\n[red]✗ Could not disengage: {e}[/red]\n")
        raise typer.Exit(1) from e
    except StaleGovernanceWriteError as e:
        console.print(
            f"\n[red]✗ Could not disengage: {e}[/red]\n"
            "[yellow]State changed since it was last read -- rerun `brake status` and retry.[/yellow]\n",
        )
        raise typer.Exit(1) from e

    console.print("\n[green]✓ Parking brake DISENGAGED[/green] - All components allowed\n")


@brake_app.command("status")
def brake_status(
    db: str = typer.Option("data/bartholomew.db", help="Path to database file"),
):
    """Show parking brake status"""
    from bartholomew.orchestrator.safety.governance_store import GovernanceStore

    store = GovernanceStore(db)
    state = store.state()

    console.print("\n[bold]Parking Brake Status[/bold]")
    console.print(f"Database: {db}\n")

    if state.engaged:
        console.print("[yellow]Status: ENGAGED (blocking)[/yellow]")
        console.print(f"Scopes: {', '.join(sorted(state.scopes))}\n")
    else:
        console.print("[green]Status: DISENGAGED (allowing all)[/green]\n")


@app.command("train")
def train(
    file: str = typer.Argument(
        ...,
        help="Path to a JSON training submission "
        "(competency_id, source_type, source_detail, records[])",
    ),
    db: str = typer.Option("data/bartholomew.db", help="Path to database file"),
):
    """
    Submit structured training material for a competency (S5.2).

    Takes already-structured records -- this command does not extract
    records from prose. Per the approved S5.2 design that is a scope
    boundary, not the intended final user experience: conversational and
    document-based training are expected later, feeding this same governed
    path rather than a separate one.

    Records that trip a consent rule are queued for review in the consent
    inbox rather than stored, and are reported separately below: "queued"
    means Bartholomew does NOT yet know it.
    """
    import asyncio
    import json
    from pathlib import Path

    from bartholomew.kernel import training as training_mod
    from bartholomew.kernel.memory_store import MemoryStore
    from bartholomew.kernel.runtime_contract import run_training_through_runtime_contract

    try:
        payload = json.loads(Path(file).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        console.print(f"[red]Could not read submission: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    competency_id = payload.get("competency_id", "")

    try:
        records = [
            training_mod.record_from_payload(
                item["kind"],
                {**item.get("data", {}), "competency_id": competency_id},
                slug=item.get("slug"),
            )
            for item in payload.get("records", [])
        ]
    except (ValueError, KeyError, TypeError) as exc:
        console.print(f"[red]Invalid record in submission: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    submission = training_mod.TrainingSubmission(
        competency_id=competency_id,
        source_type=payload.get("source_type", ""),
        source_detail=payload.get("source_detail", ""),
        records=records,
    )

    class _NoExperience:
        """The CLI has no running Experience Kernel. These return empties
        rather than being absent so _build_interpretation() takes its normal
        'nothing to add' path instead of logging an AttributeError traceback
        per field -- the enrichment is genuinely unavailable here, which is
        not an error worth alarming the user with. Governance and consent
        are unaffected either way."""

        def get_active_goals(self):
            return []

        def get_active_pack_id(self):
            return None

        def get_context_string(self):
            return ""

    class _CliDaemon:
        """Minimal context for the seam: MemoryStore plus empty Experience
        Kernel state."""

        def __init__(self, mem):
            self.mem = mem
            self.experience = _NoExperience()
            self.persona_manager = _NoExperience()
            self.working_memory = _NoExperience()

    async def _run():
        mem = MemoryStore(db)
        await mem.init()
        try:
            return await run_training_through_runtime_contract(
                _CliDaemon(mem),
                submission,
                recorded_by="user",
            )
        finally:
            await mem.close()

    result = asyncio.run(_run())

    if result.errors:
        console.print("[red]Submission rejected:[/red]")
        for error in result.errors:
            console.print(f"  - {error}")
        raise typer.Exit(code=1)

    if not result.governance_allowed:
        console.print(f"[red]Blocked by governance: {result.governance_reason}[/red]")
        raise typer.Exit(code=1)

    table = Table(title=f"Training: {competency_id}")
    table.add_column("Kind")
    table.add_column("Key")
    table.add_column("Outcome")
    table.add_column("Detail")
    for outcome in result.outcomes:
        colour = "green" if outcome.outcome == training_mod.OUTCOME_STORED else "yellow"
        table.add_row(
            outcome.kind,
            outcome.key,
            f"[{colour}]{outcome.outcome}[/{colour}]",
            outcome.detail or "",
        )
    console.print(table)

    summary = result.to_dict()["summary"]
    console.print(
        f"\nstored: {summary['stored']} · "
        f"queued for consent: {summary['queued_for_consent']} · "
        f"rejected: {summary['rejected_by_policy']} · "
        f"invalid: {summary['invalid']}",
    )
    if summary["queued_for_consent"]:
        console.print(
            "[yellow]Queued records are awaiting your approval in the consent inbox "
            "and are NOT yet part of what Bartholomew knows.[/yellow]",
        )


def main():
    """Entry point for CLI"""
    app()


if __name__ == "__main__":
    main()
