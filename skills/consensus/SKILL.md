---
name: consensus
description: Use when a decision is expensive to reverse and one model's answer is not enough — architecture splits, storage or codec choices, a design the user doubts, a claim that sounds too confident. Also use when the user asks for a second opinion, another model, other models, GPT, Grok, Gemini, консенсус, второе мнение, спроси другие модели, что скажут другие.
license: MIT
metadata:
  author: Alexander Malaev
  version: "0.1.2"
---

# Consensus of several models

A panel of models from different vendors answers one question. Then each model reads the other
answers and revises its own. The panel runs on the Claude Code and Cursor subscriptions.
It needs no API key.

The disagreements carry the value. The agreement does not. The models learn from overlapping
data, so they make the same mistake together. Never present agreement as proof.

## When to use the panel

- The decision is expensive to reverse: an architecture split, a storage choice, a training setup.
- You have a recommendation, but you are not sure. A wrong choice costs days of work.
- The user asks for a second opinion. The user asks what other models say.

## When not to use the panel

- You can measure the fact. You can run a test. You can read the documentation. Measure instead.
- The task is mechanical: a change by example, a rename, a known bug.
- You need speed. One run takes about three minutes and six full answers from the quota.

## How to run the panel

The question is about code in a repository. Then always use `-d`. This is measured: the panel
with `-d` found the precedent that the project set already. The panel without `-d` answered
from the textbook and missed it.

Run `scripts/consensus.sh` from this skill directory. Claude Code installs the skill under
`~/.claude/skills/consensus`, so the full path is `~/.claude/skills/consensus/scripts/consensus.sh`.

```bash
# Repository, read-only. This is the default form:
scripts/consensus.sh -d "$PWD" -q "вопрос" -r 2

# Without -d the panel sees only the files you pass. Use this form for sensitive code:
scripts/consensus.sh -q "вопрос" -f path/to/file -r 2
```

Set the Bash timeout to 900000 ms or more. Run `-h` for the flags and the default panel.

## Requirements

- `claude` — the Claude Code CLI, for a `claude:` participant.
- `agent` — the Cursor CLI, for a `cursor:` participant. Log in with `agent login`.
- `jq` — the script reads the Cursor answer from JSON with it.
- `timeout` or `gtimeout` — optional. Without either one the model calls run with no time
  limit and the script says so on stderr. macOS ships neither until `brew install coreutils`.

A participant whose CLI is missing gets no answer, and the script says so for that participant
only. A panel of one harness is legitimate: `-m claude:fable -r 1` is a single reader with
read-only access to the repository, which is an audit rather than a consensus.

Both CLIs run on their own subscription. The panel needs no API key.
The Cursor model ids depend on the account. List your own with `agent models`.
Set `CONSENSUS_PARTICIPANTS` to change the default panel.

## How to write the question

The models know nothing about this conversation. They know nothing about the project.
The question must read on its own.

1. Name the stack, the volumes, and the limits. Write "Elixir/OTP, up to 50 thousand members".
   Do not write "our chat".
2. Name the entry points: the modules, the directories, the tests. A model that gets no entry
   point reads the wrong files in a large tree.
3. Tell the panel what you tried already. Tell the panel why it did not work.
4. Limit the length of the answer. Ask each model to name the main risk of its own choice.
   The question about risk shows the difference between two models that chose the same option.

## How to write the synthesis

You write the synthesis. The script prints the material and stops.

- **Agreement** — one line for each point. Do not retell the answers.
- **Disagreement** — the main part. Name who claims what. Name the basis of each claim:
  a measurement, the documentation, or an assumption.
- **What would settle it** — the measurement or the source that closes the question.
  Give the exact command, if you know it.
- **Your own recommendation** — with a reason. Do not report the majority as the reason.

Write the synthesis in the language that the user uses.

A participant gave no answer. Then say so. Do not give that participant an opinion.

## Common mistakes

| Mistake | Do this instead |
|---|---|
| A panel of models from one vendor | Use different vendors. One vendor gives false agreement |
| A question with "our" and "this" | Write the full question. It must read without the session |
| A synthesis that retells three answers | Agreement, disagreement, what settles it, recommendation |
| "All three agree, so it is correct" | The training data overlaps. Agreement is not proof |
| A panel instead of a measurement | Measure first. Use the panel for a judgement |

## Safety

Both harnesses run read-only: Claude without `Edit`, `Write` and `Bash`, Cursor with `--mode ask`.
This is tested — each one reads a file, and each one refuses to create a file. The Cursor flag
`--sandbox enabled` does **not** block a write. Do not treat it as protection.

With `-d` the panel sees the whole repository, gitignored files included. For sensitive code,
drop `-d` and pass the files with `-f`.
