"""Claude Code hooks shipped with devagent.

`enforce_local` is a PreToolUse hook that — only when a repo opts in via a `.devagent/ENFORCE`
sentinel — blocks the host agent from hand-writing source files, forcing implementation through
the local-model `devagent` pipeline. This is the structural enforcement of the decompose-execute
skill: it removes the host's discretion instead of relying on it."""
