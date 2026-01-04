from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Optional, Self

import toml_rs as toml
import yaml
from pydantic import BaseModel, ConfigDict, computed_field, model_validator

from bpm import logger


def rebase_path(new_root: Path | str, subdir: Path | str) -> Path:
    """
    Rebase a path onto a new root.

    Arguments
    ---------
    :param Path | str new_root: The new root to rebase the subdirectory onto.
    :param Path | str subdir: The subdirectory to rebase.

    Returns
    -------
    :returns Path: The rebased path. If the subdirectory was already relative to the new root, it's returned as-is.
    """
    # Temporarily convert thie subdir to a Path (if it isn't already).
    if not isinstance(subdir, Path):
        subdir = Path(subdir)
    try:
        # If the subdir is already relative to the new root, just return it as-is.
        subdir.relative_to(new_root)
        return subdir
    except ValueError:
        # In this case, the subdir is *not* already relative, so we need to fix it.
        pass
    

    # if (
    #     (isinstance(subdir, Path) and subdir.relative_to(new_root))
    #     or (not isinstance(subdir, Path) and Path(subdir).relative_to(new_root))
    # ):
    #     return Path(subdir)
    
    # Root needs to be of type Path.
    if not isinstance(new_root, Path):
        new_root = Path(new_root)


    # Subdir needs to be a string, as we're doing some manual mangling.
    if not isinstance(subdir, str):
        subdir = str(subdir)

    # original_path = str(data['path'])
    if subdir[0] == "/":
        rootless_path = subdir[1:]
    else:
        rootless_path = subdir

    return new_root / rootless_path


class Action(BaseModel):
    name: str
    """Action name."""
    module_name: str
    """What module is this action from?"""
    cmd: str
    """Command to run."""
    args: Optional[bool] = False
    """Should additional arguments be passed to this action?"""
    watch_cmd: Optional[str] = None
    """If this action has a "watch" variant, define that here."""
    work_dir: Path
    """What directory should this action be ran in? If not set explicitly, it is inherited by the parent element."""
    bg: Optional[bool] = False
    """Can this command run simultaneously with other tasks (i.e. "in the background)? Only matters when running in an action group."""

    model_config = ConfigDict(
        # https://docs.pydantic.dev/latest/api/config/#pydantic.config.ConfigDict.frozen
        frozen=True
    )

    def __str__(self) -> str:
        return f"{self.module_name}.{self.name}"  # ({self.cmd})"


class Module(BaseModel):
    name: str
    work_dir: Path
    actions: dict[str, Action]

    @model_validator(mode="before")
    @classmethod
    def set_additional_action_fields(cls, data: dict) -> Any:
        """
        Some fields of each Action config are not set in the config (by design), but are extremely helpful when actually
        using that config in code. Some fields are only optional in the config, but are very much required here. This
        *before validator* sets those fields.

        Fields
        ------

        - `work_dir`: If each Action config does not manually set its working directory, set it to this Module's working directory.
        - `name`: Set the Action's name. The name's value is taken from the key defining this Action's config. Ex: From the config
          ```
          { "actions": { "demo": { "cmd": "exit 0" }}}
          ```
          the name would be "demo".
        - `module_name`: Set this to the module's name. Helpful when trying to figure out what Module each Action is associated with.
        """
        
        repo_root = get_git_repo_root()

        if not isinstance(data, dict):
            raise TypeError("Not dict.")

        # Grab the raw action configs
        actions: dict[str, dict[str, Any]] = dict(data["actions"])
        module_name: str = data["name"]

        for k, a in actions.items():
            # If the `work_dir` property isn't set, set it to this module's working dir. Otherwise, rebase it onto the Git repo.
            if not a.get("work_dir"):
                a["work_dir"] = data["work_dir"]
            else:
                a["work_dir"] = rebase_path(repo_root, a["work_dir"])

            # Set the action's `name` property to its key in the dictionary.
            a["name"] = k
            # Set the parent module's name in the raw action data.
            a["module_name"] = module_name
            # Make sure the action is updated in the dictionary.
            actions[k] = a

        # Make sure the modified actions are saved to the raw data.
        data["actions"] = actions

        return data

    @model_validator(mode="before")
    def rebase_workdir_onto_git_repo_root(cls, data: dict) -> Any:
        """
        This *before validator* just rebases the working directory onto the git repository.
        """
        
        assert isinstance(data, dict)
        repo_root = get_git_repo_root()
        data["work_dir"] = str(rebase_path(repo_root, data["work_dir"]))
        return data

    @model_validator(mode="after")
    def assert_workdir_exists(self) -> Self:
        """
        Make sure the configured working directory actually exists.
        """
        
        if not self.work_dir.exists():
            repo_root = get_git_repo_root()
            raise ValueError(
                f"The workdir path '{self.work_dir.relative_to(repo_root)}' does not exist within the git repo root '{repo_root}'"
            )

        return self


class BPMConfig(BaseModel):
    modules: dict[str, Module]

    @model_validator(mode="before")
    @classmethod
    def add_module_names_to_modules(cls, data: dict) -> Any:
        """
        Some fields of each Module config are not set in the config (by design), but are extremely helpful when actually
        using that config in code. This *before validator* sets those fields.

        Fields
        ------

        - `name`: Set the Module's name. The name's value is taken from the key defining this Module's config. Ex: From the config
          ```
          { "modules": { "demo": { ... }}}
          ```
          the name would be "demo".

        """
        assert isinstance(data, dict)

        # Grab the raw module data.
        modules: dict[str, dict[str, Any]] = dict(data["modules"])

        for k, m in modules.items():
            # Set the `name` property, and make sure it saves.
            m["name"] = k

            # Make sure the updated module data is saved.
            modules[k] = m

        # Make sure the modified modules are saved to the raw data.
        data["modules"] = modules

        return data

    @computed_field
    @property
    def all_actions(self) -> dict[str, list[Action]]:
        """
        All actions, collected from all modules. If two or more modules have actions with the same name,
        they are treated as one and will be ran one-after-the-other.
        """

        cmds: dict[str, list[Action]] = {}
        for m in self.modules.values():
            for c_name, c_data in m.actions.items():
                # try:
                #     cmds.get(c_name, None)
                # except KeyError:
                #     cmds[c_name] = []
                if not cmds.get(c_name):
                    cmds[c_name] = []

                cmds[c_name] += [c_data]

        return cmds

    @computed_field
    @property
    def action_groups(self) -> dict[str, list[Action]]:
        """
        All actions that have been defined in two or more modules are considered part of an action group.

        For example, a `dev` action could launch dev servers for backend and frontend components simultaneously.
        """

        return {n: d for n, d in self.all_actions.items() if len(d) > 1}


def get_git_repo_root() -> Path:
    proc = subprocess.run(
        args=["git", "rev-parse", "--show-toplevel"],
        stdout=subprocess.PIPE,
        encoding="utf-8",
    )

    if proc.returncode > 0:
        raise FileNotFoundError("Not in a Git repository.")

    return Path(proc.stdout.strip())


def load_yaml(yaml_file: Path | str):
    if not isinstance(yaml_file, Path):
        yaml_file = Path(yaml_file)

    with open(yaml_file, "r") as y:
        return yaml.safe_load(y)


def load_toml(toml_file: Path | str):
    if not isinstance(toml_file, Path):
        toml_file = Path(toml_file)

    with open(toml_file, "r") as t:
        return toml.loads(t.read())


def load_config(config_file: Optional[Path | str] = None):
    if not config_file:
        repo_root = get_git_repo_root()

        if (p := repo_root / "bpm.toml").exists():
            config_file = p
        elif (p := repo_root / "bpm.yml").exists() or (
            p := repo_root / "bpm.yaml"
        ).exists():
            config_file = p

        # Try searching for it with glob. Pick the first one found, if any.
        found_files = (
            [f for f in Path.cwd().rglob("bpm.toml")]
            + [f for f in Path.cwd().rglob("bpm.yml")]
            + [f for f in Path.cwd().rglob("bpm.yaml")]
        )

        if not found_files:
            raise FileNotFoundError("Config file not found in repo")
        else:
            config_file = found_files[0]

    else:
        if not isinstance(config_file, Path):
            config_file = Path(config_file)

        if not config_file.exists():
            raise FileNotFoundError(f"Config file {config_file} does not exist.")

    # The `config_file` variable should be set and be of type `Path`.
    assert isinstance(config_file, Path)

    logger.info(f"Using '{config_file}'")

    if config_file.suffix == ".toml":
        data = load_toml(config_file)
    elif config_file.suffix == (".yml" or ".yaml"):
        data = load_yaml(config_file)

    # data = load_yaml(config_file)

    return BPMConfig.model_validate(data)


def convert_toml_to_yaml(toml_file: Path, yaml_file: Path):
    if not isinstance(yaml_file, Path):
        yaml_file = Path(yaml_file)
    if not isinstance(toml_file, Path):
        toml_file = Path(toml_file)

    with (
        open(toml_file, "r") as t,
        open(yaml_file, "w+") as y,
    ):
        data = toml.loads(t.read())
        yaml.safe_dump(
            data=data,
            stream=y,
        )


def convert_yaml_to_toml(yaml_file: Path, toml_file: Path):
    if not isinstance(yaml_file, Path):
        yaml_file = Path(yaml_file)
    if not isinstance(toml_file, Path):
        toml_file = Path(toml_file)

    with (
        open(yaml_file, "r") as y,
        open(toml_file, "w+") as t,
    ):
        data = yaml.safe_load(y)
        toml.dump(data, t)
