from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import threading
import time
from typing import Optional

from environs import Env
from termcolor import colored

from bpm import logger
from bpm.config import Action, BPMConfig, Module, load_config

# If running on Windows, make sure ANSI colors are enabled.
if os.name == "nt":
    os.system("color")


class Args:
    module: Optional[Module]
    """The selected module."""
    actions: list[Action]
    """The selected action. If an action group was selected, this contains a list of the actual actions underneath the action group."""
    args: list[str] = []
    """Optional additional arguments to pass to this action."""

    def __init__(self, config: BPMConfig):
        parser = argparse.ArgumentParser()

        parser.add_argument("--module", "-m", type=str)
        parser.add_argument("action", help="Action (or group action) to run.")
        parser.add_argument(
            "args",
            nargs="*",
            help="Additional arguments to pass to the action, if allowed.",
        )

        args = parser.parse_args()

        if args.module:
            # Is this module configured?
            if args.module not in config.modules.keys():
                logger.error(
                    f"Unknown module '{args.module}'. Allowed options are: {', '.join(config.modules.keys())}",
                    exiting=True,
                )
            self.module = config.modules[args.module]

            # Is this action configured?
            if args.action not in self.module.actions.keys():
                logger.error(
                    f"Action '{args.action}' is not defined for module '{args.module}'. Available actions are: {', '.join(self.module.actions.keys())}",
                    exiting=True,
                )
            a = self.module.actions[args.action]
            self.actions = [a]

            # If args were given, make sure the action allows that.
            if args.args and not a.args:
                logger.error(
                    f"The '{args.action}' action on module '{args.module}' does not allow additional arguments, but {len(args.args)} were given.",
                    exiting=True,
                )
            self.args = args.args

        else:
            ags = config.action_groups

            # Is the given action name actually an action group?
            if args.action not in ags.keys():
                logger.error(
                    f"The '{args.action}' action is not used by more than one module. To run it, use the `-m <module>` argument to run it within that module's context.",
                    exiting=True,
                )
            self.actions = ags[args.action]

            # If args were passed, make sure at least one of the actions within the action group is configured to accept them.
            # If one or more action is not, display a warning, but continue anyways.
            if args.args:
                actions_accepting_args = [
                    (a.args if a.args else False) for a in self.actions
                ]
                if not any(actions_accepting_args):
                    logger.error(
                        f"None of the actions within the '{args.action}' action group accept additional arguments.",
                        exiting=True,
                    )
                elif not all(actions_accepting_args):
                    yes = sum(actions_accepting_args)
                    no = len(self.actions) - sum(actions_accepting_args)
                    logger.warn(
                        f"Additional arguments were given to the '{args.action}' action group, but only {yes} of {len(self.actions)} actions accept them. The arguments will be ignored for the other {'action' if no <= 1 else f'{no} actions'}."
                    )
            self.args = args.args


class ActionRunner:
    actions: list[Action]
    """Actions ran and managed by this ActionRunner."""
    can_run_simultaneously: bool
    """Can these actions run simultaneously?"""
    action_args: list[str]
    """Args to pass to each action, if applicable and given."""
    processes: dict[Action, subprocess.Popen] = {}
    """Running process objects for each action."""

    def __init__(self, args: Args):
        # if not isinstance(actions, list):
        #     actions = [actions]
        self.actions = args.actions
        self.can_run_simultaneously = all([a.bg for a in self.actions])
        self.action_args = args.args

    @staticmethod
    def _stream_output(stream, prefix: str):
        """
        Read from a stream and print each line with a prefix.
        """
        try:
            for line in iter(stream.readline, b""):
                if line:
                    print(f"[{prefix}] {line.decode('utf-8').rstrip()}")
        except Exception as e:
            logger.error(f"[{prefix}] Error reading stream: {e}")
        finally:
            stream.close()

    def run(self):
        """
        Run the selected commands.
        """

        if self.can_run_simultaneously:
            logger.info("Running all actions within the action group simultaneously.\n")
            try:
                # Start all processes
                for action in self.actions:
                    # Create a prefix for this action's output
                    prefix = f"{action.module_name}.{action.name}"

                    logger.info(f"Running action {action}")

                    command: list[str] = shlex.split(action.cmd) + self.action_args

                    # Start the process in the action's intended CWD.
                    try:
                        proc = subprocess.Popen(
                            args=command,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            cwd=action.work_dir,
                        )
                    except FileNotFoundError as e:
                        missing_file = str(e.filename)
                        logger.error(
                            # str(e.strerror) + missing_file,
                            f"{e.strerror}: {missing_file}",
                            exiting=True,
                        )

                    # Start threads to follow stdout and stderr
                    stdout_thread = threading.Thread(
                        target=self._stream_output,
                        args=(proc.stdout, prefix),
                        daemon=True,
                    )
                    stderr_thread = threading.Thread(
                        target=self._stream_output,
                        args=(
                            proc.stderr,
                            f"{prefix} {colored('ERR', 'red')}",
                        ),  # make those error messages pop!
                        daemon=True,
                    )

                    stdout_thread.start()
                    stderr_thread.start()

                    self.processes[action] = proc

                # Wait for all processes to complete
                while self.processes:
                    # Check each process
                    for action, proc in list(self.processes.items()):
                        # poll() returns None if still running, otherwise returns exit code
                        if proc.poll() is not None:
                            logger.info(
                                f"Action {action} completed with exit code {proc.returncode}"
                            )
                            del self.processes[action]

                    # Small sleep to avoid busy waiting
                    if self.processes:
                        time.sleep(0.1)

                print()
                logger.info("All actions completed.")

            except KeyboardInterrupt:
                print()  # Print a newline before the WARN message.
                logger.warn("Received interrupt, killing all child processes...")
                self._kill_all_processes()
                sys.exit(1)
        else:
            logger.info(
                "One or more actions within the action group cannot run simultaneously. Running them in series."
            )
            for action in self.actions:
                # Create a prefix for this action's output
                # prefix = f"{action.module_name}.{action.name}"
                print()
                logger.info(f"Running action {action}")
                command: list[str] = shlex.split(action.cmd) + self.action_args

                try:
                    proc = subprocess.run(
                        args=command,
                        cwd=action.work_dir,
                    )
                    logger.info(
                        f"Action {action} completed with exit code {proc.returncode}"
                    )
                except FileNotFoundError as e:
                    missing_file = str(e.filename)
                    logger.error(
                        f"{e.strerror}: {missing_file}",
                        exiting=True,
                    )

            print()
            logger.info("All processes completed")

    def _kill_all_processes(self):
        """
        Kill all running child processes.
        """

        for action, proc in self.processes.items():
            try:
                # Send SIGTERM first (graceful)
                proc.terminate()
                # Wait up to 3 seconds for graceful termination
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    # If still running, force kill with SIGKILL
                    proc.kill()
                    proc.wait()
                logger.info(f"Killed process for {action} (PID: {proc.pid})")
            except Exception as e:
                logger.error(f"Error killing process for {action}: {e}", exiting=False)
        self.processes.clear()


def main():
    env = Env()
    BPM_CONFIG_PATH = env.str("BPM_CONFIG_PATH", None)
    config = load_config(BPM_CONFIG_PATH)

    args = Args(config)

    runner = ActionRunner(args)
    runner.run()


if __name__ == "__main__":
    main()
