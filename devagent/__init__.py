"""devagent — cost-efficient multi-model coding CLI.

Core thesis: a local model (Qwen) produces frontier-quality output *inside its parity
envelope* — small, well-scoped tasks. The job of the system is to keep every task inside
that envelope (precise retrieval, file windowing, task decomposition), verify every output
through a deterministic gate, and consult a frontier model only to decompose hard tasks or
when the gate fails.
"""

__version__ = "0.1.0"
