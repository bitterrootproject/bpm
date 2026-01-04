# bpm

BPM is a simple, script-based monorepo helper tool. It was created for the Bitterroot Project to make managing the mixed-language monorepo more tolerable.

Unlike other monorepo managers, BPM doesn't really care what languages, build systems, or package managers your monorepo uses, as it's all script-based. You write a single config file, in either YAML or TOML format, defining each component of your repo (backend, frontend, desktop app, documentation, etc.) and their Makefile-esque [**actions**](#actions-and-action-groups). Each action has some additional settings, like what directory to run it in, if additional arguments can be passed, etc. More details on this will come later as development progresses.

The config file can be set manually with the `BPM_CONFIG_PATH` environment variables.

## Actions and Action Groups

An **action** is an alias to one shell command (running multiple commands comming soon!), as well as some associated options to augment how that command runs. An action has the following options:

```yml
actions:
  action_name: &demo_action
    # The shell command.
    cmd: bash -c 'echo Wow!'

    # If this has a "watchable" variant (such as built:watch),
    # you can define it here:
    watch_cmd: bash -c "echo I'm watching you."

    # If the command accepts additional arguments, enable this.
    args: true

    # Enable if the command can run "in the background" with one or more other tasks.
    bg: false


    # ----------------
    #  FUTURE OPTIONS
    # ----------------

    # Use Git-diff-awareness to determine whether or not
    # this action needs to run.
    git: false

    # Run multiple commands as part of this action.
    cmd:
    - bash -c 'echo One'
    - bash -c 'echo Two'
```

Let's say, for the sake of this example, this action is defined in the `demo` module:

```yml
modules:
  demo:
    # All paths are relative to the Git repo's root.
    work_dir: /
    actions:
      action_name: *demo_action
```

> [!NOTE]
> Curious about that special YAML syntax with the "&amp;" and "&ast;"? Those are [anchors and aliases](https://www.educative.io/blog/advanced-yaml-syntax-cheatsheet#YAML-Anchors-and-Alias)!

Now, to run this action, use the following command:

```shell
bpm -m demo action_name
```

If we had defined another module with an action of the same name, we could run:

```shell
bpm action_name
```

as BPM then considers `action_name` to be an **action group**.

### Action Groups

An **action group** is a collection of actions which are all defined in different modules and all share the same name. This lets you run one command to do a bunch of stuff back-to-back &mdash; or even simultaneously, if all actions within the action group have `bg = True`.