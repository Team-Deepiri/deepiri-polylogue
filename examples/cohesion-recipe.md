# Cohesion recipe (multi-window)

Goal: three different LLM surfaces (any providers) co-own one task.

1. In the repo root, run once:

   ```bash
   polylogue init --session my-feature
   polylogue join --id cursor-gpt --label "Cursor GPT" --provider openai
   polylogue join --id web-claude --label "Browser Claude" --provider anthropic
   polylogue join --id local-llm --label "Ollama Qwen" --provider ollama
   ```

2. Before each model replies, paste the output of:

   ```bash
   polylogue sync-pack
   ```

3. After each model finishes a meaningful slice of work:

   ```bash
   polylogue say --id cursor-gpt --role assistant --text "Implemented CLI; tests pass."
   ```

4. When explicitly passing work:

   ```bash
   polylogue handoff --id cursor-gpt --to web-claude --text "Please review error handling in cli.py."
   ```

5. Optional state pin:

   ```bash
   polylogue snapshot --summary "CI green on main @ abc1234"
   ```

Every surface reads the same files; cohesion is **protocol + habit**, not vendor magic.
