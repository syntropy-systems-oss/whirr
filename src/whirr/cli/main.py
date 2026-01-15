# Copyright (c) Syntropy Systems
"""Main CLI entry point for whirr."""

import typer

from whirr.cli.ablate import ablate_app
from whirr.cli.cancel import cancel
from whirr.cli.compare import compare
from whirr.cli.dashboard import dashboard
from whirr.cli.doctor import doctor
from whirr.cli.export import export
from whirr.cli.init_cmd import init
from whirr.cli.logs import logs
from whirr.cli.retry import retry
from whirr.cli.runs import runs, show
from whirr.cli.server_cmd import server
from whirr.cli.status import status
from whirr.cli.submit import submit
from whirr.cli.sweep import sweep
from whirr.cli.watch import watch
from whirr.cli.worker import worker

app = typer.Typer(
    name="whirr",
    help=(
        "Local experiment orchestration. Queue jobs, track metrics, "
        "wake up to results."
    ),
    no_args_is_help=True,
    add_completion=False,
)

# Register commands
_ = app.command()(init)
_ = app.command(
    context_settings={"allow_extra_args": True, "allow_interspersed_args": False}
)(submit)
_ = app.command()(status)
_ = app.command()(worker)
_ = app.command()(logs)
_ = app.command()(cancel)
_ = app.command()(retry)
_ = app.command()(sweep)
_ = app.command()(watch)
_ = app.command()(runs)
_ = app.command()(show)
_ = app.command()(doctor)
_ = app.command()(dashboard)
_ = app.command()(compare)
_ = app.command(name="export")(export)
_ = app.command()(server)

# Register ablate sub-app
app.add_typer(ablate_app, name="ablate")


if __name__ == "__main__":
    app()
