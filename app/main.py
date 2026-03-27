"""
CLI entry point — defines the `process` command via Typer.

Usage:
    python run.py process "<youtube_url>"
"""

import typer
from rich.console import Console
from rich.panel import Panel

from app.utils.logger import get_logger

app = typer.Typer(
    name="youtube-arabic-transcriber",
    help="Download a YouTube video and produce cleaned Arabic transcript, TL;DR, Twitter thread, and FAQ.",
    add_completion=False,
)
console = Console()
logger = get_logger(__name__)


@app.command()
def process(
    url: str = typer.Argument(..., help="YouTube video URL to process"),
    provider: str = typer.Option(
        "zai",
        "--provider",
        help="LLM provider: zai (default) or openai",
    ),
    transcription_mode: str = typer.Option(
        "balanced",
        "--transcription-mode",
        help="Transcription mode: fast | balanced (default) | quality",
    ),
) -> None:
    """
    Download and transcribe a YouTube video to transcript-first outputs.

    Outputs are saved to outputs/<video_title>/.
    """
    # Defer imports so startup is fast and config errors surface cleanly
    from app.config import Settings
    from app.pipeline import run_pipeline
    from app.services.transcriber import normalize_transcription_mode

    console.print(
        Panel.fit(
            "[bold cyan]YouTube → Arabic Transcription Engine[/bold cyan]",
            subtitle="starting pipeline",
        )
    )

    try:
        settings = Settings()
    except Exception as exc:
        console.print(f"[bold red]Configuration error:[/bold red] {exc}")
        console.print(
            "Make sure [bold].env[/bold] exists and all required variables are set.\n"
            "See [bold].env.example[/bold] for reference."
        )
        raise typer.Exit(code=1)

    provider = provider.strip().lower()
    if provider not in {"zai", "openai"}:
        console.print("[bold red]Invalid provider:[/bold red] use zai or openai")
        raise typer.Exit(code=1)

    try:
        transcription_mode = normalize_transcription_mode(transcription_mode)
    except Exception as exc:
        console.print(f"[bold red]Invalid transcription mode:[/bold red] {exc}")
        raise typer.Exit(code=1)

    try:
        result = run_pipeline(
            url,
            settings,
            provider=provider,
            transcription_mode=transcription_mode,
        )
    except Exception as exc:
        logger.exception("Pipeline failed")
        console.print(f"\n[bold red]Pipeline failed:[/bold red] {exc}")
        raise typer.Exit(code=1)

    console.print(
        Panel.fit(
            f"[bold green]Done![/bold green]\n\n"
            f"[bold]Title:[/bold] {result.metadata.title}\n"
            f"[bold]Output:[/bold] {result.output_dir}",
            title="[green]Success[/green]",
        )
    )
    console.print("\nGenerated files:")
    for fname in [
        "raw_transcript.txt",
        "transcript_ar.md",
        "metadata.json",
    ]:
        console.print(f"  • {result.output_dir}/{fname}")
