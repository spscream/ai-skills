#!/usr/bin/env bash
# Опрос нескольких моделей по одному вопросу, в два раунда.
#
# Раунд 1: каждая модель отвечает независимо.
# Раунд 2: каждой модели показывают чужие ответы обезличенно и просят пересмотреть свой.
# Синтез делает вызывающая сторона: скрипт печатает материал, а не вывод.
#
# Участники задаются как <обвязка>:<модель>:
#   claude:opus                     — подписка Claude Code, через `claude -p`
#   cursor:gpt-5.6-sol-high         — подписка Cursor, через `agent -p --model`
#
# Набор по умолчанию собран по разным вендорам: Anthropic, OpenAI, xAI.
# Однородная панель даёт ложное согласие, поэтому вендоры берутся разные.
# Ещё варианты из того же аккаунта: gemini-3.7-flash-high (Google),
# kimi-k3-max (Moonshot), glm-5.2-max (Zhipu). Полный список — `agent models`.

set -uo pipefail

QUESTION=""
REPO=""
declare -a CONTEXT_FILES=()
# Идентификаторы моделей Cursor зависят от аккаунта. Свои смотрите командой `agent models`.
PARTICIPANTS="${CONSENSUS_PARTICIPANTS:-claude:opus,cursor:gpt-5.6-sol-high,cursor:cursor-grok-4.6-high}"
ROUNDS=2
TIMEOUT=600
OUTDIR=""

usage() {
  cat <<'EOF'
Usage: consensus.sh -q "вопрос" [опции]

  -q "текст"     вопрос (обязателен; либо подать вопрос на stdin)
  -f файл        файл контекста, копируется в рабочий каталог; можно повторять
  -d каталог     работать в этом каталоге и видеть репозиторий целиком.
                 Модели запускаются в режиме только для чтения. Без -d они сидят
                 во временном каталоге и видят лишь то, что передано через -f
  -m список      участники через запятую, вида <обвязка>:<модель>.
                 По умолчанию берётся CONSENSUS_PARTICIPANTS, иначе
                 claude:opus,cursor:gpt-5.6-sol-high,cursor:cursor-grok-4.6-high.
                 Модели своего аккаунта Cursor смотрите командой `agent models`
  -r N           число раундов: 1 — только независимые ответы, 2 — с пересмотром (по умолчанию 2)
  -t секунды     таймаут одного вызова модели (по умолчанию 600)
  -o каталог     куда сложить ответы (по умолчанию временный каталог)
EOF
}

while getopts "q:f:d:m:r:t:o:h" opt; do
  case "$opt" in
    q) QUESTION="$OPTARG" ;;
    f) CONTEXT_FILES+=("$OPTARG") ;;
    d) REPO="$OPTARG" ;;
    m) PARTICIPANTS="$OPTARG" ;;
    r) ROUNDS="$OPTARG" ;;
    t) TIMEOUT="$OPTARG" ;;
    o) OUTDIR="$OPTARG" ;;
    h) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
done

if [[ -z "$QUESTION" ]] && [[ ! -t 0 ]]; then
  QUESTION="$(cat)"
fi

if [[ -z "$QUESTION" ]]; then
  echo "consensus.sh: вопрос не задан" >&2
  usage >&2
  exit 2
fi

# Предохранитель от рекурсии.
#
# Сейчас участник и так не может запустить панель: у claude нет Bash, а cursor идёт в режиме ask,
# где выполнение команд запрещено. Обе проверки сделаны прогоном. Но обе держатся на одной строке
# флагов ниже. Если её ослабить, вложенный запуск размножит вызовы: три участника дают девять
# прогонов на втором уровне и двадцать семь на третьем.
#
# Переменная наследуется любым дочерним процессом, поэтому вложенный запуск умрёт сразу.
if [[ -n "${CONSENSUS_DEPTH:-}" ]]; then
  echo "consensus.sh: вложенный запуск панели запрещён (CONSENSUS_DEPTH=$CONSENSUS_DEPTH)" >&2
  exit 3
fi
export CONSENSUS_DEPTH=1

export PATH="$HOME/.local/bin:$PATH"

# `timeout` есть не везде. На macOS его нет ни под этим именем, ни под `gtimeout`,
# пока не поставлен coreutils. Голый вызов давал бы код 127 на каждом участнике, а
# обработчик ошибок ниже написал бы «участник не ответил» — то есть отказ среды
# выглядел бы как отказ моделей, и панель молча возвращала бы пустой материал.
# Поэтому обёртка выбирается один раз, и её отсутствие — предупреждение, не отказ.
TIMEOUT_CMD=""
if command -v timeout >/dev/null 2>&1; then
  TIMEOUT_CMD="timeout"
elif command -v gtimeout >/dev/null 2>&1; then
  TIMEOUT_CMD="gtimeout"
else
  echo "consensus.sh: ни timeout, ни gtimeout не найдены — вызовы моделей идут без ограничения по времени (macOS: brew install coreutils)" >&2
fi

# Запуск с ограничением по времени, если оно доступно, и без него, если нет.
run_limited() {
  if [[ -n "$TIMEOUT_CMD" ]]; then
    "$TIMEOUT_CMD" "$TIMEOUT" "$@"
  else
    "$@"
  fi
}

# Рабочий каталог отдельный: в неинтерактивном режиме обе обвязки имеют доступ на запись.
# Пусть пишут сюда, а не в репозиторий.
if [[ -n "$OUTDIR" ]]; then
  mkdir -p "$OUTDIR"
  WORK="$OUTDIR"
else
  WORK="$(mktemp -d -t consensus-XXXXXX)"
  # Убрать за собой только свой временный каталог. Каталог из -o задан
  # пользователем: он его и просил, значит он остаётся.
  trap 'rm -rf "$WORK"' EXIT
fi
# Путь обязан стать абсолютным здесь. Участники запускаются после `cd` в рабочий каталог,
# и относительный путь из -o после этого указывает не туда: редирект падает, ответы теряются.
WORK="$(cd "$WORK" && pwd)"

mkdir -p "$WORK/r1" "$WORK/r2" "$WORK/context"

for f in "${CONTEXT_FILES[@]:-}"; do
  [[ -n "$f" ]] || continue
  if [[ -f "$f" ]]; then
    cp "$f" "$WORK/context/"
  else
    echo "consensus.sh: файл контекста не найден: $f" >&2
    exit 2
  fi
done

# Каталог, из которого работают модели.
# По умолчанию временный: он и есть изоляция.
# С -d это сам репозиторий, и тогда изоляцию даёт режим только для чтения у каждой обвязки.
RUNDIR="$WORK"
if [[ -n "$REPO" ]]; then
  if [[ ! -d "$REPO" ]]; then
    echo "consensus.sh: каталог не найден: $REPO" >&2
    exit 2
  fi
  RUNDIR="$(cd "$REPO" && pwd)"
fi

IFS=',' read -r -a PARTS <<< "$PARTICIPANTS"

slug() { echo "$1" | tr ':/ ' '___'; }

# Отсутствие обвязки — отказ среды, и он обязан называться своим именем.
# Та же причина, что и с timeout выше: без проверки `claude`, `agent` или `jq`
# дают ненулевой код или пустой файл, обработчик ниже пишет «участник не
# ответил», и отказ среды выглядит отказом моделей. Проверяем один раз, до
# первого раунда, чтобы не узнавать об этом трижды параллельно.
missing_tool() {
  echo "consensus.sh: не найдено: $1 — $2" >&2
}
DEPS_OK=1
for p in "${PARTS[@]}"; do
  case "${p%%:*}" in
    claude)
      command -v claude >/dev/null 2>&1 || { missing_tool claude "нужен для участника $p"; DEPS_OK=0; }
      ;;
    cursor)
      command -v agent >/dev/null 2>&1 || { missing_tool agent "нужен для участника $p"; DEPS_OK=0; }
      # jq извлекает .result из ответа cursor: без него ответ есть, но не разобран.
      command -v jq >/dev/null 2>&1 || { missing_tool jq "нужен для разбора ответа cursor ($p)"; DEPS_OK=0; }
      ;;
    *)
      echo "consensus.sh: неизвестная обвязка в участнике $p" >&2
      DEPS_OK=0
      ;;
  esac
done
if [[ "$DEPS_OK" -ne 1 ]]; then
  echo "consensus.sh: панель не запущена — сначала поставьте недостающее или измените -m" >&2
  exit 4
fi

# Один вызов модели. $1 — участник, $2 — файл с промптом, $3 — файл для ответа.
ask() {
  local participant="$1" prompt_file="$2" out_file="$3"
  local harness="${participant%%:*}" model="${participant#*:}"
  local rc=0

  case "$harness" in
    claude)
      # Только чтение: Edit, Write и Bash не выданы, поэтому запись отклоняется правами.
      # Промпт идёт через stdin: --allowedTools забирает все следующие аргументы как имена инструментов.
      run_limited claude -p --model "$model" --allowedTools "Read Grep Glob" \
        < "$prompt_file" > "$out_file" 2> "$out_file.err"
      rc=$?
      ;;
    cursor)
      # --mode ask — режим только для чтения. Он обязателен: проверено, что без него
      # `-p --sandbox enabled` спокойно создаёт файлы в рабочем каталоге.
      # --trust снимает вопрос про доверие к каталогу.
      run_limited agent -p --mode ask --model "$model" --trust --sandbox enabled \
        --output-format json "$(cat "$prompt_file")" \
        > "$out_file.json" 2> "$out_file.err"
      rc=$?
      if [[ $rc -eq 0 ]]; then
        jq -r '.result // empty' < "$out_file.json" > "$out_file" 2>/dev/null || rc=1
      fi
      ;;
    *)
      echo "неизвестная обвязка: $harness" > "$out_file.err"
      rc=2
      ;;
  esac

  if [[ $rc -ne 0 ]] || [[ ! -s "$out_file" ]]; then
    {
      echo "ОШИБКА: участник $participant не ответил (код $rc)."
      echo "stderr:"
      head -c 2000 "$out_file.err" 2>/dev/null
    } > "$out_file"
  fi
}

# --- Раунд 1: независимые ответы -------------------------------------------

R1_PROMPT="$WORK/prompt-r1.txt"
{
  echo "You are a member of a panel. Give your own answer."
  echo "Do not run consensus.sh. Do not start a second panel. Recursion is not permitted here."
  echo "Do not call a memory server. This panel does not read or write another person's memory."
  echo
  echo "Answer the question below. Keep to the point. Be short."
  echo "Name the basis of your conclusion: a measurement, the documentation, or an assumption."
  echo "Name the data that is missing, if data is missing."
  echo "Write your answer in the language of the question."
  echo
  if [[ -n "$REPO" ]]; then
    echo "You work in the root of the repository $RUNDIR. You have read access only."
    echo "Read the files that you need. Name each file and each line that you use."
    echo "Do not read .agent-memory, .env, or a file with keys. They do not relate to the question."
    echo
  fi
  if [[ -n "$(ls -A "$WORK/context" 2>/dev/null)" ]]; then
    echo "The context files are in the directory $WORK/context. Read them before you answer."
    echo
  fi
  echo "=== QUESTION ==="
  echo "$QUESTION"
} > "$R1_PROMPT"

declare -a PIDS=()
for p in "${PARTS[@]}"; do
  ( cd "$RUNDIR" && ask "$p" "$R1_PROMPT" "$WORK/r1/$(slug "$p").md" ) &
  PIDS+=($!)
done
for pid in "${PIDS[@]}"; do wait "$pid"; done

# --- Раунд 2: пересмотр с чужими ответами ----------------------------------

if [[ "$ROUNDS" -ge 2 ]]; then
  # Ответы обезличены: авторство модели смещает оценку.
  PEERS="$WORK/peers.txt"
  : > "$PEERS"
  i=0
  for p in "${PARTS[@]}"; do
    i=$((i + 1))
    {
      echo "=== ANSWER OF PARTICIPANT $i ==="
      cat "$WORK/r1/$(slug "$p").md"
      echo
    } >> "$PEERS"
  done

  declare -a PIDS2=()
  j=0
  for p in "${PARTS[@]}"; do
    j=$((j + 1))
    prompt_file="$WORK/prompt-r2-$(slug "$p").txt"
    {
      echo "You answered the question below before. You were participant $j."
      echo "The answers of all participants are below. Your own answer is one of them."
      echo "Read them all. Then do three things."
      echo "1. Tell the reader whether you change your conclusion. Give the reason."
      echo "2. Name each point where you disagree. Give the basis of your disagreement."
      echo "3. Give your final answer."
      echo "Do not agree only to agree. A disagreement with a reason beats a common opinion."
      echo "Do not run consensus.sh. Do not start a second panel. Recursion is not permitted here."
      echo "Write your answer in the language of the question."
      echo
      echo "=== QUESTION ==="
      echo "$QUESTION"
      echo
      cat "$PEERS"
    } > "$prompt_file"

    ( cd "$RUNDIR" && ask "$p" "$prompt_file" "$WORK/r2/$(slug "$p").md" ) &
    PIDS2+=($!)
  done
  for pid in "${PIDS2[@]}"; do wait "$pid"; done
fi

# --- Вывод -----------------------------------------------------------------

echo "# Material for the synthesis"
echo
echo "Question:"
echo
echo "$QUESTION"
echo
echo "Participants: $PARTICIPANTS"
echo "Rounds: $ROUNDS"
echo "Directory: $WORK"
echo
for p in "${PARTS[@]}"; do
  echo "## $p — round 1"
  echo
  cat "$WORK/r1/$(slug "$p").md"
  echo
done

if [[ "$ROUNDS" -ge 2 ]]; then
  for p in "${PARTS[@]}"; do
    echo "## $p — round 2"
    echo
    cat "$WORK/r2/$(slug "$p").md"
    echo
  done
fi
