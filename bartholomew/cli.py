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


from bartholomew.cli_trust import devices_app, groups_app, share_app

app = typer.Typer(help="Bartholomew Admin CLI")
console = Console()
embeddings_app = typer.Typer(help="Embeddings management commands")
brake_app = typer.Typer(help="Parking brake safety controls")
accounts_app = typer.Typer(help="Alpha account provisioning (operator only)")
platform_brake_app = typer.Typer(help="Platform/Admin parking brake (all users)")
app.add_typer(embeddings_app, name="embeddings")
app.add_typer(brake_app, name="brake")
app.add_typer(accounts_app, name="accounts")
app.add_typer(platform_brake_app, name="platform-brake")
# Package E. The commands live in `bartholomew/cli_trust.py` rather than here:
# this file is a shared integration hotspot, and three registration lines are
# a smaller thing for every other stream to merge around than six hundred.
app.add_typer(devices_app, name="devices")
app.add_typer(groups_app, name="groups")
app.add_typer(share_app, name="share")

multimodal_app = typer.Typer(help="Multimodal presence (microphone, screen, speech)")
app.add_typer(multimodal_app, name="multimodal")


@embeddings_app.command("stats")
def embeddings_stats(
    db: str = typer.Option("data/bartholomew.db", help="Path to database file"),
):
    """Show embeddings statistics and the live retrieval mode"""
    import os
    import sqlite3

    from bartholomew.kernel.embedding_engine import KIND_UNVERIFIED, get_embedding_status
    from bartholomew.kernel.retrieval import describe_retrieval
    from bartholomew.kernel.vector_store import VectorStore

    console.print("\n[bold]Embeddings Statistics[/bold]")
    console.print(f"Database: {db}\n")

    # The truthful mode, from the same accessor /api/health reads -- never
    # reconstructed from configuration here, so the two cannot disagree.
    status = get_embedding_status()
    colour = "green" if status.semantic else ("yellow" if status.degraded else "cyan")
    console.print(f"Mode: [{colour}]{status.mode.value}[/{colour}]")
    console.print(f"Semantic retrieval: {'yes' if status.semantic else 'NO'}")
    console.print(f"Provider: {status.provider}")
    console.print(f"Model: {status.model}")
    console.print(f"Dimension: {status.dim}")
    if status.reason:
        console.print(f"Reason: {status.reason}")

    try:
        described = describe_retrieval()
        console.print(
            f"Retrieval mode: {described['mode_configured']} configured, "
            f"{described['mode_effective']} effective",
        )
    except Exception as e:
        console.print(f"[red]Could not resolve retrieval mode: {e}[/red]")

    if not os.getenv("BARTHO_EMBED_ENABLED"):
        console.print("\n[yellow]Enable with: BARTHO_EMBED_ENABLED=1[/yellow]\n")

    # Check VSS availability
    try:
        vec_store = VectorStore(db)
        vss_status = "\u2713 enabled" if vec_store.vss_available else "\u2717 disabled"
        console.print(f"SQLite VSS: {vss_status}")
    except Exception as e:
        console.print(f"[red]Error loading vector store: {e}[/red]")
        return

    # Database stats
    if not os.path.exists(db):
        console.print(f"\n[yellow]Database not found: {db}[/yellow]\n")
        return

    # The honest inventory: what is actually retrievable, and what is not.
    try:
        by_kind = vec_store.count_by_kind()
        if by_kind:
            console.print("\n[bold]Embeddings by embedder kind:[/bold]")
            for kind, count in sorted(by_kind.items()):
                note = ""
                if kind == KIND_UNVERIFIED:
                    note = (
                        "  [yellow](excluded from retrieval -- run `embeddings rebuild`)[/yellow]"
                    )
                console.print(f"  {kind}: {count}{note}")
    except Exception as e:
        console.print(f"[red]Error reading embedding kinds: {e}[/red]")

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


@embeddings_app.command("provision")
def embeddings_provision(
    target: str = typer.Option(
        None,
        help="Directory to write the model into (default: embeddings.yaml model_path)",
    ),
):
    """Download the configured embedding model into a local directory.

    This is the ONE place model assets are fetched. Ordinary retrieval never
    downloads anything: an unprovisioned model fails closed and reports itself,
    rather than making first-run startup depend on an uncontrolled several
    hundred megabyte fetch. Run this deliberately, once, per deployment.
    """
    from bartholomew.kernel.embedding_engine import _embedding_factory

    cfg = _embedding_factory._load_config()
    destination = target or cfg.model_path

    if not destination:
        console.print(
            "[red]No destination.[/red] Set `embeddings.model_path` in "
            "embeddings.yaml (or BARTHO_EMBED_MODEL_PATH), or pass --target.",
        )
        raise typer.Exit(code=1)

    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        console.print(
            "[red]sentence-transformers is not installed.[/red]\n"
            "Install the embeddings extra first:  pip install -e '.[embeddings]'",
        )
        raise typer.Exit(code=1) from None

    console.print(f"Fetching [cyan]{cfg.model}[/cyan] into [magenta]{destination}[/magenta]")
    console.print("[yellow]This downloads model assets over the network.[/yellow]")

    try:
        # Deliberately NOT wrapped in `_hub_offline`: this command exists
        # precisely to be the authorised online step.
        model = SentenceTransformer(cfg.model, device="cpu")
        model.save(destination)
    except Exception as e:
        console.print(f"[red]Provisioning failed: {e}[/red]")
        raise typer.Exit(code=1) from e

    console.print(f"[green]Provisioned.[/green] Model saved to {destination}")
    if not cfg.model_path:
        console.print(
            "Set `embeddings.model_path` (or BARTHO_EMBED_MODEL_PATH) to this "
            "path so ordinary startup loads it.",
        )
    console.print("Verify with:  bartholomew embeddings stats")


@embeddings_app.command("rebuild")
def embeddings_rebuild(
    db: str = typer.Option("data/bartholomew.db", help="Path to database file"),
    dry_run: bool = typer.Option(False, help="Report what would change, write nothing"),
):
    """Regenerate embeddings that cannot honestly be used for retrieval.

    Targets two populations:

      `unverified`        rows predating the embedder_kind column, whose true
                          embedder is unknowable from the row;
      mismatched kinds    rows produced by a different embedder than the one
                          now configured (e.g. deterministic-hash rows left
                          over from before a real model was provisioned).

    Each is re-embedded from the authoritative retained source text on the
    memory itself. Where no such text is retained, the row is left alone and
    reported: it stays excluded from retrieval rather than being presented as
    something it is not.

    Never deletes a memory. A row that cannot be regenerated is excluded, not
    destroyed, so nothing is lost that a later provisioning run could recover.
    """
    import sqlite3

    from bartholomew.kernel.embedding_engine import (
        EmbedderUnavailableError,
        get_embedding_engine,
        get_embedding_status,
    )
    from bartholomew.kernel.vector_store import VectorStore

    status = get_embedding_status()
    console.print(f"\n[bold]Rebuild embeddings[/bold]  (database: {db})")
    console.print(f"Current embedder: {status.mode.value} -- {status.provider}/{status.model}")

    if status.mode.value == "unavailable":
        console.print(f"[red]Cannot rebuild: {status.reason}[/red]")
        raise typer.Exit(code=1)

    if not status.semantic:
        # Rebuilding into the deterministic embedder is legitimate for
        # development, but it must be a deliberate, visible choice -- not
        # something that quietly refills the store with non-semantic vectors.
        console.print(
            "[yellow]Warning:[/yellow] the current embedder is NOT semantic. "
            "Rebuilt vectors will be stored as deterministic-hash and will not "
            "provide meaning-based retrieval.",
        )

    try:
        engine = get_embedding_engine()
    except EmbedderUnavailableError as e:
        console.print(f"[red]Cannot rebuild: {e}[/red]")
        raise typer.Exit(code=1) from e

    provider, model, embedder_kind = engine.storage_identity
    vec_store = VectorStore(db)

    # Candidates: anything not already carrying the current embedder's kind.
    try:
        with sqlite3.connect(db) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT e.embedding_id, e.memory_id, e.source, e.embedder_kind,
                       m.summary, m.value
                FROM memory_embeddings e
                LEFT JOIN memories m ON m.id = e.memory_id
                WHERE e.embedder_kind != ?
                ORDER BY e.embedding_id
                """,
                (embedder_kind,),
            ).fetchall()
    except sqlite3.Error as e:
        console.print(f"[red]Database error: {e}[/red]")
        raise typer.Exit(code=1) from e

    if not rows:
        console.print(
            "[green]Nothing to rebuild.[/green] Every embedding matches the current embedder.\n",
        )
        return

    console.print(f"Candidates: {len(rows)}")

    rebuilt = 0
    skipped: list[tuple[int, str]] = []

    for row in rows:
        # Authoritative retained source text, preferring the summary -- the
        # same precedence the write path uses, so a rebuilt vector matches what
        # a fresh write would have produced.
        text = (row["summary"] or "") if row["source"] == "summary" else (row["value"] or "")
        if not text.strip():
            text = (row["value"] or "").strip()[:500]

        if not text.strip():
            # No retained source: cannot regenerate honestly. Leave the row
            # excluded rather than inventing a vector for it.
            skipped.append((row["memory_id"], "no retained source text"))
            continue

        if dry_run:
            rebuilt += 1
            continue

        try:
            vec = engine.embed_texts([text])[0]
            vec_store.upsert(
                row["memory_id"],
                vec,
                row["source"],
                provider,
                model,
                embedder_kind,
            )
            rebuilt += 1
        except Exception as e:
            skipped.append((row["memory_id"], str(e)))

    verb = "would be rebuilt" if dry_run else "rebuilt"
    console.print(f"[green]{rebuilt}[/green] embedding(s) {verb}.")

    if skipped:
        console.print(
            f"[yellow]{len(skipped)}[/yellow] left excluded from retrieval "
            "(no memory was deleted):",
        )
        for memory_id, reason in skipped[:10]:
            console.print(f"  memory {memory_id}: {reason}")
        if len(skipped) > 10:
            console.print(f"  ... and {len(skipped) - 10} more")

    console.print()


@embeddings_app.command("evaluate")
def embeddings_evaluate(
    db: str = typer.Option(None, help="Scratch database to seed (default: a temp file)"),
):
    """Measure retrieval behaviour against the bounded evaluation fixture.

    Reports top-1 and top-3 behaviour per mode and per query category, together
    with the embedding status that produced those numbers -- because a score
    without the embedder that produced it is not evidence of anything.

    This measures; it does not gate. There is no pass mark here, and relevance
    thresholds must not be tuned until these numbers say what tuning them would
    actually do.
    """
    import tempfile
    from pathlib import Path

    from bartholomew.kernel.retrieval_eval import EVAL_MODES, run_evaluation

    try:
        from tests.fixtures.retrieval_eval_corpus import CASES, CORPUS
    except ImportError:
        console.print(
            "[red]Evaluation fixture not found.[/red] "
            "It ships with the tests; run this from a source checkout.",
        )
        raise typer.Exit(code=1) from None

    with tempfile.TemporaryDirectory() as tmp:
        db_path = db or str(Path(tmp) / "retrieval-eval.db")
        results = run_evaluation(db_path, CORPUS, CASES, modes=EVAL_MODES)

    retrieval = results["retrieval"]
    embedding = retrieval["embedding"]

    console.print("\n[bold]Retrieval evaluation[/bold]")
    console.print(
        f"Embedder: [cyan]{embedding['mode']}[/cyan] "
        f"({embedding['provider']}/{embedding['model']}), "
        f"semantic={'yes' if embedding['semantic'] else 'NO'}",
    )
    console.print(
        f"Retrieval: {retrieval['mode_configured']} configured, "
        f"{retrieval['mode_effective']} effective",
    )
    if retrieval["degraded"]:
        console.print(f"[yellow]Degraded:[/yellow] {retrieval['reason']}")
    console.print(
        f"Corpus: {results['corpus_size']} memories, {results['case_count']} cases\n",
    )

    summary = Table(title="Top-1 / Top-3 by mode (answerable cases)")
    summary.add_column("Mode", style="cyan")
    summary.add_column("Top-1", style="green")
    summary.add_column("Top-3", style="green")
    summary.add_column("Notes", style="yellow")

    for mode, report in results["reports"].items():
        if report.error:
            summary.add_row(mode, "-", "-", f"could not run: {report.error[:60]}")
            continue
        noise = sum(case.returned_anything for case in report.irrelevant)
        summary.add_row(
            mode,
            f"{report.top1:.0%}" if report.top1 is not None else "-",
            f"{report.top3:.0%}" if report.top3 is not None else "-",
            f"{noise}/{len(report.irrelevant)} irrelevant queries returned something",
        )

    console.print(summary)

    for mode, report in results["reports"].items():
        if report.error:
            continue
        table = Table(title=f"{mode}: top-1 / top-3 by category")
        table.add_column("Category", style="cyan")
        table.add_column("Cases")
        table.add_column("Top-1", style="green")
        table.add_column("Top-3", style="green")
        for category, (count, t1, t3) in sorted(report.by_category().items()):
            table.add_row(category, str(count), f"{t1}/{count}", f"{t3}/{count}")
        console.print(table)

    console.print()


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


@app.command("say")
def say(
    text: str = typer.Argument(..., help="What Bartholomew should say out loud"),
    db: str = typer.Option("data/bartholomew.db", help="Path to database file"),
    config: str = typer.Option("config/kernel.yaml", help="Path to kernel config"),
    identity: str = typer.Option("Identity.yaml", help="Path to Identity file"),
):
    """
    Say something out loud on this machine (local text-to-speech).

    Output only: this opens no microphone, records nothing, and reaches no
    device other than this machine's own audio output.

    Three things must line up before a sound is made, and this command tells
    you which one stopped it if none is:

      1. config/kernel.yaml's `voice.spoken_output` is true (default false);
      2. the `voice` Parking Brake scope is not engaged;
      3. Identity.yaml's tool_use.allowlist contains "voice_speak".

    A machine with no local speech binary installed reports "no engine"
    rather than silently appearing to have spoken.
    """
    import asyncio

    import yaml

    from bartholomew.kernel import spoken_output
    from bartholomew.kernel.runtime_contract import (
        run_spoken_output_through_runtime_contract,
    )

    try:
        cfg = yaml.safe_load(open(config, encoding="utf-8")) or {}
    except OSError as exc:
        console.print(f"[red]Could not read {config}: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    identity_context = None
    try:
        from identity_interpreter.identity_context import build_identity_context
        from identity_interpreter.loader import load_identity

        identity_context = build_identity_context(load_identity(identity))
    except Exception:
        # Same posture as every other surface: an Identity Context that
        # cannot be built is simply not consulted (the check is additive),
        # rather than being treated as an allow.
        console.print(f"[yellow]Could not load {identity}; Identity policy not consulted.[/yellow]")

    result = asyncio.run(
        run_spoken_output_through_runtime_contract(
            text,
            enabled=spoken_output.enabled_for(cfg),
            db_path=db,
            identity_context=identity_context,
        ),
    )

    if result.started:
        engine = getattr(result.result, "engine", None) or "unknown engine"
        console.print(f"[green]Spoken via {engine}.[/green]")
        return

    console.print(f"[yellow]Nothing was spoken: {result.reason or result.outcome}[/yellow]")
    if not spoken_output.enabled_for(cfg):
        console.print(
            f"[dim]Set `voice.spoken_output: true` in {config} to allow spoken output.[/dim]",
        )
    elif spoken_output.available_engine() is None:
        console.print(
            "[dim]No local speech binary found. On Debian/Ubuntu: "
            "`sudo apt-get install espeak-ng`. On macOS, `say` is built in.[/dim]",
        )
    raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# Alpha account provisioning (S8)
# ---------------------------------------------------------------------------
#
# Operator-only, and local-only by construction: these commands talk to the
# control-plane database on disk, not over HTTP. There is deliberately no
# remote account-management endpoint -- an authenticated remote surface that
# can create accounts is an authenticated remote surface that can create an
# account for an attacker.


@accounts_app.command("create")
def accounts_create(
    username: str = typer.Argument(..., help="Alpha participant's username"),
    admin: bool = typer.Option(
        False,
        "--admin",
        help="Create a platform administrator instead of an ordinary user. "
        "A distinct authority kind, not a user with extra powers: it has no "
        "personal Bartholomew and cannot read anyone's memory.",
    ),
    password: str = typer.Option(
        None,
        help="Password. Omit to generate a strong one and print it once.",
    ),
):
    """Provision an Alpha account."""
    from bartholomew.platform import accounts as _accounts
    from bartholomew.platform.principal import PrincipalKind
    from bartholomew.platform.store import init_platform_schema

    init_platform_schema()
    generated = password is None
    secret = password or _accounts.generate_password()
    kind = PrincipalKind.PLATFORM_ADMIN if admin else PrincipalKind.USER

    try:
        user_id = _accounts.create_account(username, secret, kind=kind)
    except _accounts.AccountError as e:
        console.print(f"\n[red]✗ {e}[/red]\n")
        raise typer.Exit(1) from e

    console.print(f"\n[green]✓ Created {kind.value}[/green] {username}")
    console.print(f"  user_id: {user_id}")
    if generated:
        # Printed once, never stored anywhere in readable form.
        console.print(f"  password: [bold]{secret}[/bold]")
        console.print("  [yellow]Shown once. Record it now.[/yellow]")
    console.print()


@accounts_app.command("list")
def accounts_list():
    """List Alpha accounts. Never prints password material."""
    from bartholomew.platform.accounts import list_accounts
    from bartholomew.platform.store import init_platform_schema

    init_platform_schema()
    table = Table(title="Alpha accounts")
    for col in ("username", "kind", "user_id", "status"):
        table.add_column(col)
    for row in list_accounts():
        table.add_row(
            row["username"],
            row["kind"],
            row["user_id"],
            "disabled" if row["disabled_at"] else "active",
        )
    console.print(table)


@accounts_app.command("disable")
def accounts_disable(
    user_id: str = typer.Argument(..., help="user_id from `accounts list`"),
):
    """
    Disable an account and revoke every live session it holds.

    Immediate: the next request on any of that account's sessions fails.
    """
    from bartholomew.platform.accounts import set_account_disabled
    from bartholomew.platform.store import init_platform_schema

    init_platform_schema()
    set_account_disabled(user_id, True)
    console.print(f"\n[yellow]⚠ Account disabled and sessions revoked:[/yellow] {user_id}\n")


@accounts_app.command("enable")
def accounts_enable(
    user_id: str = typer.Argument(..., help="user_id from `accounts list`"),
):
    """Re-enable a disabled account. Previously revoked sessions stay revoked."""
    from bartholomew.platform.accounts import set_account_disabled
    from bartholomew.platform.store import init_platform_schema

    init_platform_schema()
    set_account_disabled(user_id, False)
    console.print(f"\n[green]✓ Account enabled:[/green] {user_id}\n")


# ---------------------------------------------------------------------------
# Platform/Admin parking brake tier
# ---------------------------------------------------------------------------
#
# A separate command group from `brake`, against a separate store, on
# purpose. `bartholomew brake off` is a user releasing their own halt and
# must never release a platform-wide one.


@platform_brake_app.command("on")
def platform_brake_on(
    scope: list[str] = typer.Option(None, "--scope", help="Scopes to halt platform-wide"),
    reason: str = typer.Option(None, help="Why this halt was engaged"),
    actor: str = typer.Option(..., help="Who is engaging it (recorded in the audit trail)"),
):
    """Engage the Platform/Admin halt across every user's Bartholomew."""
    from bartholomew.platform import authority
    from bartholomew.platform.store import init_platform_schema

    init_platform_schema()
    authority.install_platform_halt_hook()
    state = authority.engage(*(scope or ["global"]), reason=reason, actor=actor)
    console.print(
        f"\n[red]⚠ PLATFORM halt ENGAGED[/red] - scopes: "
        f"{', '.join(sorted(state.scopes))} (revision {state.revision})\n",
    )


@platform_brake_app.command("off")
def platform_brake_off(
    reason: str = typer.Option(None, help="Why this halt was released"),
    actor: str = typer.Option(..., help="Who is releasing it (recorded in the audit trail)"),
    expected_revision: int = typer.Option(
        None,
        help="Refuse if the platform brake has moved since you read it",
    ),
):
    """Release the Platform/Admin halt. Does not touch any Personal brake."""
    from bartholomew.platform import authority
    from bartholomew.platform.store import init_platform_schema

    init_platform_schema()
    try:
        state = authority.disengage(reason=reason, actor=actor, expected_revision=expected_revision)
    except authority.StalePlatformWriteError as e:
        console.print(f"\n[yellow]{e}[/yellow]\n")
        raise typer.Exit(1) from e
    console.print(f"\n[green]✓ PLATFORM halt RELEASED[/green] (revision {state.revision})\n")


@platform_brake_app.command("status")
def platform_brake_status():
    """Show the Platform/Admin halt state."""
    from bartholomew.platform import authority
    from bartholomew.platform.store import init_platform_schema

    init_platform_schema()
    state = authority.get_state()
    if state.engaged:
        console.print(
            f"\n[red]PLATFORM halt ENGAGED[/red] - scopes: "
            f"{', '.join(sorted(state.scopes))} (revision {state.revision})\n",
        )
    else:
        console.print(f"\n[green]PLATFORM halt released[/green] (revision {state.revision})\n")


@app.command("serve")
def serve_command(
    host: str = typer.Option(
        None,
        help=(
            "Bind address. Defaults to the access boundary's resolution "
            "(loopback unless BARTH_API_ALLOW_NON_LOOPBACK is deliberately set)."
        ),
    ),
    port: int = typer.Option(None, help="Port to listen on. Defaults to BARTH_API_PORT or 5173."),
    workers: int = typer.Option(1, help="Must be 1; Bartholomew's persistence is single-writer."),
    reload: bool = typer.Option(False, "--reload", help="Refused; see `serve`'s error message."),
    log_level: str = typer.Option("info", help="uvicorn log level."),
) -> None:
    """
    Run Bartholomew as a service.

    The entry point a service supervisor launches: no terminal, no browser, no
    reload. Bartholomew's kernel and scheduler run in this process and keep
    running whether or not anything is connected to it.

    Supervision (restart on failure, start at boot) belongs to systemd, the
    Windows service manager, or the container runtime -- see `deploy/README.md`.
    This command does not supervise itself.
    """
    from bartholomew.runtime.serve import serve

    code = serve(host=host, port=port, workers=workers, reload=reload, log_level=log_level)
    if code != 0:
        raise typer.Exit(code=code)


@app.command("unattended-report")
def unattended_report_command(
    run_id: str = typer.Argument(..., help="The BARTH_UNATTENDED_RUN_ID the run used."),
    db: str = typer.Option("data/bartholomew.db", help="Path to the runtime database file."),
    out: str = typer.Option(None, help="Write the frozen report here instead of stdout."),
    item_limit: int = typer.Option(200, help="Max rows inlined per evidence source."),
) -> None:
    """
    Freeze the evidence record for an unattended run.

    Reads the run ledger and the records the runtime already wrote (ticks,
    governance audit, governed skill actions, inbound events, startup
    incidents) and seals them into one deterministic JSON document with a
    digest over its content. Read-only: it never writes to, corrects, or
    reconciles the runtime's own records.

    Run it after the run has stopped. An incarnation that is still open is
    reported as still open -- which, for a run that is supposed to be over,
    is the finding.
    """
    import json as _json

    from bartholomew.runtime.evidence_report import freeze, write_frozen_report

    if out:
        envelope = write_frozen_report(db, run_id, out, item_limit=item_limit)
        console.print(f"[green]Frozen[/green] {out}")
    else:
        envelope = freeze(db, run_id, item_limit=item_limit)
        print(_json.dumps(envelope, indent=2, sort_keys=True, default=str))

    summary = envelope["record"]["summary"]
    complete = summary["complete"]
    colour = "green" if complete else ("yellow" if complete is None else "red")
    console.print(f"digest: {envelope['digest']}")
    console.print(f"[{colour}]{summary['verdict']}[/{colour}]")


@multimodal_app.command("diagnose")
def multimodal_diagnose(
    json_output: bool = typer.Option(False, "--json", help="Emit the raw report as JSON"),
):
    """Report which multimodal capabilities work on this machine, and why not.

    Package C's required diagnostic command (contract §7). It observes
    nothing: it asks the operating system whether devices and optional
    dependencies exist, without opening an audio stream, reading the
    accessibility tree or capturing any image. Running it needs no session and
    no consent because it collects nothing about the user.
    """
    import json as _json

    from bartholomew.multimodal.diagnostics import diagnose, format_report

    report = diagnose()
    if json_output:
        print(_json.dumps(report, indent=2, sort_keys=True, default=str))
    else:
        console.print(format_report(report))


@multimodal_app.command("status")
def multimodal_status_command():
    """Show whether Bartholomew is listening, observing the screen or speaking.

    Sessions live in the process that owns the device, so this reports on the
    CLI's own process -- which owns none. It will therefore always report
    nothing active, plus this machine's hardware availability. To see a running
    daemon's sessions, read GET /api/multimodal/status on that process. That
    distinction is deliberate: a status command that guessed about another
    process's capture state would be exactly the kind of claim this package
    must never make.
    """
    from bartholomew.multimodal.status import status_snapshot
    from bartholomew.multimodal.store import SessionStore

    snapshot = status_snapshot(SessionStore())
    console.print(snapshot["summary"])
    console.print(
        "(This is the CLI process. For the running daemon's sessions, "
        "GET /api/multimodal/status)",
    )
    hardware = snapshot["hardware"]
    console.print(
        f"microphone: {hardware['microphone']['availability']} -- "
        f"{hardware['microphone']['detail']}",
    )
    console.print(
        f"spoken output: {'available' if hardware['spoken_output']['available'] else 'unavailable'}"
        f" -- {hardware['spoken_output']['detail']}",
    )


def main():
    """Entry point for CLI"""
    app()


if __name__ == "__main__":
    main()
