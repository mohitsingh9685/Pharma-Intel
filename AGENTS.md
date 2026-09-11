# Project working agreement

## Learning workflow

The user wants to learn while building this project.

- Work in complete, coherent milestones: finish the initial setup, then build
  complete features. Do not fragment related setup into tiny lessons or turns.
  Keep unrelated features outside the current milestone.
- Keep a brisker pace: group closely related setup, code, and verification into
  one meaningful increment. Avoid extra turns for trivial pin-only changes or
  repeated checks when the user's existing output is sufficient.
- Work toward a complete setup or feature while explaining its connected parts.
  Do not turn each file edit or dependency pin into a separate checkpoint.
- Prepare the related files and one ordered command sequence together. Pause
  only when user-run results or an unresolved requirement are necessary to
  proceed correctly, not merely because one small edit is finished.
- Before changing files, briefly explain the current step and its purpose.
- Prepare the files, run routine verification, then explain the changes and
  results before moving on. Provide only important control commands for the user.
- Include a short section labelled "lesson for u" in every substantive final
  response. Use simple words to explain what changed, what the relevant files
  do, and how the code, data, or request flow connects them.
- Introduce technical terms with a plain-language explanation. Keep each lesson
  focused on the current increment and identify one next step.
- Answer questions about the current step before introducing more components.

## Command execution and configuration

- The assistant runs routine local development commands, including migration
  generation, migrations, checks, and focused automated tests.
- The user runs important learning and control commands: dependency installation
  or upgrades, secret and account creation, Docker lifecycle operations, Git
  commits and pushes, destructive operations, and production actions, unless the
  user explicitly delegates one of them.
- In each "lesson for u", list commands the assistant ran and explain their
  purpose and result. For commands reserved for the user, state the working
  directory, execution order, and expected success condition. If no user command
  is needed, say so.
- Do not repeat commands the user has already completed successfully, including
  changing directories, activating the virtual environment, installing unchanged
  dependencies, or rerunning unaffected checks. Provide only commands introduced
  or made necessary by the current changes, with prerequisites stated briefly.
- When a command fails, continue from the failed command after the issue is fixed
  rather than asking the user to rerun the entire earlier sequence.
- Never claim a command passed without its output. Resolve routine command
  failures during the same increment when possible.
- Clearly distinguish changes made to files from commands provided but not run.
  Review the user's output and resolve issues within the same learning step.
- Provide Git review, staging, commit, and push commands at meaningful milestones:
  once the full initial setup is complete, then once each feature is complete
  and its relevant checks have passed. Do not request a push after individual
  setup steps, file edits, dependency pins, or lessons.
- The user executes all Git commands. Use an explicit list of milestone files,
  keep secrets and local artifacts excluded, and never suggest force-pushing
  or treating a failed push as successful.

## Engineering standards

- Preserve production-quality design as the project grows. Small learning steps
  must not introduce knowingly fragile shortcuts or misleading claims of
  production readiness.
- Use supported dependencies, shared business rules, database constraints,
  transactions, and reliable background processing where appropriate.
- Prefer the latest stable releases when selecting or upgrading dependencies;
  exclude prereleases and verify compatibility. Do not label an older known
  version as the latest. When release lookups are unavailable under the user's
  command-execution workflow, obtain versions through user-run package commands,
  then record the exact resolved versions after validation.
- Run verification checks proportionate to the change's impact. Do not claim a
  check passed without evidence. Performance and concurrency claims require
  evidence against agreed workloads.
- Keep secrets and local environments out of version control.
- Consult docs/architecture-decisions.md for the accepted stack and open design
  questions. Do not silently decide company isolation or capacity targets.

## Current local environment

- Python version family: 3.14, recorded in .python-version.
- Virtual environment directory: .venv (local and untracked).
- When providing project commands, use .venv/bin/python explicitly or instruct
  the user to activate the environment first.
- The local environment is a development setup. Deployment configuration and
  dependency locking will be established in subsequent increments.
