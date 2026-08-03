<!-- build-plan:begin -->
## Active build plan — micropedia_quiz_bot
Work through every step, and confirm each is satisfied before telling the user the agent is ready.

- [ ] Create entities: quiz_channel_setting, quiz_question, quiz_score, quiz_daily_champion
- [ ] Write backend function `set_quiz_channel` — owner-only (ID 1482256878334640209) command to enable/disable a channel for quiz; persist setting to Google Drive
- [ ] Write backend function `generate_quiz_question` — call existing `_fetch_micropedia` to get a random micropedia.site article, then call the AI API (using `chat_ai_settings.api_url` + `chat_ai_settings.api_key`) with a prompt to produce a single-choice question with 4 options and a correct answer index; store as a quiz_question record
- [ ] Write backend function `check_quiz_answer` — given a quiz_question_id and a selected option, return whether the answer is correct and whether the player was the first correct answerer
- [ ] Write backend function `award_quiz_points` — award 5 points to the first correct answerer; update or create quiz_score record for today's date; persist to Google Drive
- [ ] Write backend function `get_scoreboard` — return today's top scorers and overall leaderboard from quiz_score records
- [ ] Write backend function `settle_daily_champion` — at 22:00, query quiz_score for today, find the highest scorer, create a quiz_daily_champion record, reset daily scores, and return champion data for announcement
- [ ] Authorize the Google Drive connector for score and settings persistence
- [ ] Create cron automation: every 30 minutes during active hours → run generate-and-post-quiz skill in the configured quiz channel
- [ ] Create cron automation: daily at 22:00 → run settle-daily-champion skill in the configured quiz channel
- [ ] Configure Discord bot channel with slash commands: `/set-quiz-channel`, `/scoreboard`, `/quiz-champion`
- [ ] Write operating rules to .agents/rules/quiz_policy.md, .agents/rules/owner_permissions.md, .agents/rules/scoring_integrity.md
- [ ] Test end-to-end: owner sets channel → cron fires → question posted → player answers via button → points awarded → 22:00 champion announced
<!-- build-plan:end -->