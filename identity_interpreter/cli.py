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

from .loader import IdentityLoadError, lint_identity, load_identity
from .normalizer import normalize_identity
from .policies import (
    check_tool_allowed,
    get_persona_config,
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

        # Tool policy if requested
        if tool:
            console.print(f"\n[cyan]Tool Policy for '{tool}':[/cyan]")
            tool_decision = check_tool_allowed(identity, tool)
            _print_decision(tool_decision)

        # Persona
        console.print("\n[cyan]Persona Configuration:[/cyan]")
        persona = get_persona_config(identity, context="casual")
        console.print(f"  Traits: {', '.join(persona['traits'])}")
        console.print(f"  Tone: {', '.join(persona['tone'])}")

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
