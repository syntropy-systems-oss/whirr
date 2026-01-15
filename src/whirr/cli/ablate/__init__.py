# Copyright (c) Syntropy Systems
"""whirr ablate subcommand group."""

import typer

from whirr.cli.ablate.add import add
from whirr.cli.ablate.init_cmd import init
from whirr.cli.ablate.rank import rank
from whirr.cli.ablate.run import run

ablate_app = typer.Typer(
    name="ablate",
    help="Ablation study tools for identifying causal factors.",
    no_args_is_help=True,
)

# Register subcommands
_ = ablate_app.command(name="init")(init)
_ = ablate_app.command()(add)
_ = ablate_app.command(
    context_settings={"allow_extra_args": True, "allow_interspersed_args": False}
)(run)
_ = ablate_app.command()(rank)
