"""
CLI for Identity Interpreter
Provides lint, explain, and simulate commands
"""

import json
import sys

try:
    import typer
    from rich.console import Console
except ImportError:
    print("Error: CLI dependencies not installed.")
    print("Install with: pip install typer rich")
    sys.exit(1)

from bartholomew.kernel.persona_pack import PersonaPackManager, create_default_pack
from bartholomew.kernel.policy_engine import evaluate_tool_policy

from .identity_context import build_identity_context
from .loader import IdentityLoadError, lint_identity, load_identity
from .normalizer import normalize_identity
from .policies import (
    handle_low_confidence,
    select_model,
)

app = typer.Typer(help="Bartholomew Identity Interpreter CLI")
console = Console()


@app.command()
def lint(
    identity_path: str = typer.Argument(
        "Identity.yaml",
        help="Path to identity.yaml file",
    ),
):
    """Validate and lint identity configuration"""
    console.print(f"\n[bold]Linting {identity_path}...[/bold]\n")

    try:
        # Load and validate
        load_identity(identity_path)
        console.print("[green]✓[/green] Schema validation passed")
        console.print("[green]✓[/green] Pydantic parsing passed")

        # Get warnings
        warnings = lint_identity(identity_path)

        if warnings:
            console.print(f"\n[yellow]⚠[/yellow] {len(warnings)} warnings:")
            for warning in warnings:
                console.print(f"  - {warning}")
        else:
            console.print("\n[green]✓[/green] No warnings")

        console.print("\n[bold green]Identity is valid![/bold green]\n")

    except IdentityLoadError as e:
        console.print(f"[bold red]✗ Error:[/bold red] {e}\n")
        sys.exit(1)


@app.command()
def explain(
    identity_path: str = typer.Argument(
        "Identity.yaml",
        help="Path to identity.yaml file",
    ),
    task_type: str = typer.Option(
        "general",
        help="Task type (general, code, safety_review)",
    ),
    confidence: float = typer.Option(
        0.5,
        help="Confidence score (0.0-1.0)",
    ),
    tool: str | None = typer.Option(
        None,
        help="Tool to check (e.g., web_fetch)",
    ),
):
    """Explain decisions for given scenario"""
    console.print("\n[bold]Explaining decisions for scenario:[/bold]\n")

    try:
        identity = load_identity(identity_path)
        identity = normalize_identity(identity)

        # Model selection
        console.print("[cyan]Model Selection:[/cyan]")
        model_decision = select_model(identity, task_type=task_type)
        _print_decision(model_decision)

        # Confidence policy
        console.print("\n[cyan]Confidence Policy:[/cyan]")
        confidence_decision = handle_low_confidence(identity, confidence)
        _print_decision(confidence_decision)

        # Tool policy if requested -- evaluated by the authoritative Executive-
        # side policy engine (evaluate_tool_policy), the successor to the
        # deprecated tool_policy.check_tool_allowed(). Identity publishes a
        # declarative IdentityContext; the policy engine turns it into a
        # decision. (PermissionChecker gates skill *manifests*, a different
        # concern -- it can't answer "is this tool in tool_use.allowlist".)
        if tool:
            console.print(f"\n[cyan]Tool Policy for '{tool}':[/cyan]")
            context = build_identity_context(identity)
            tool_decision = evaluate_tool_policy(context, tool)
            decision_summary = {
                "allowed": tool_decision.allowed,
                "in_allowlist": tool in context.tool_use_allowlist,
            }
            console.print(f"  Decision: {json.dumps(decision_summary, indent=2)}")
            if tool_decision.rationale:
                console.print("  Rationale:")
                for path in tool_decision.rationale:
                    console.print(f"    • {path}")
            if tool_decision.requires_consent:
                console.print("  [yellow]⚠ Requires consent[/yellow]")
            if tool_decision.reason:
                console.print(f"  [dim]{tool_decision.reason}[/dim]")

        # Persona -- tone from the authoritative persona system
        # (PersonaPackManager's active pack) so this matches the live kernel;
        # traits stay a stable identity descriptor from Identity directly.
        console.print("\n[cyan]Persona Configuration:[/cyan]")
        persona_manager = PersonaPackManager()
        if persona_manager.get_active_pack() is None:
            persona_manager.register_pack(create_default_pack())
        console.print(f"  Traits: {', '.join(identity.persona.traits)}")
        console.print(f"  Tone: {', '.join(persona_manager.get_tone())}")

        console.print()

    except IdentityLoadError as e:
        console.print(f"[bold red]Error:[/bold red] {e}\n")
        sys.exit(1)


@app.command()
def simulate(
    scenario_path: str = typer.Argument(
        ...,
        help="Path to scenario YAML file",
    ),
    identity_path: str = typer.Option(
        "Identity.yaml",
        help="Path to identity.yaml file",
    ),
):
    """Simulate decision-making for a scenario"""
    console.print(f"\n[bold]Simulating scenario: {scenario_path}[/bold]\n")
    console.print("[yellow]Note: Simulate command requires scenario file[/yellow]")
    console.print("Scenario format: YAML with task_type, confidence, tools, etc.\n")


@app.command()
def health():
    """Run system health checks"""
    from identity_interpreter.orchestrator.system_health import health_check

    console.print()
    health_check()
    console.print()


def _print_decision(decision):
    """Helper to print Decision object"""
    console.print(f"  Decision: {json.dumps(decision.decision, indent=2)}")
    if decision.rationale:
        console.print("  Rationale:")
        for path in decision.rationale:
            console.print(f"    • {path}")
    if decision.requires_consent:
        console.print("  [yellow]⚠ Requires consent[/yellow]")


def main():
    """Entry point for CLI"""
    app()


if __name__ == "__main__":
    main()
