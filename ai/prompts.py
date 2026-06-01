"""
All LLM prompts stored as constants.
"""

from __future__ import annotations

# ── System prompts ─────────────────────────────────────────────────────────────

SYSTEM_ANALYST = """You are an expert Reddit analyst AI. You analyze Reddit data to produce \
concise, factual, and insightful intelligence reports. Be objective and avoid speculation. \
Keep responses brief and formatted for Discord embeds (no markdown headers, use bullet points)."""

SYSTEM_SENTIMENT = """You are a sentiment and tone classifier. Respond ONLY with valid JSON. \
No explanation, no markdown, just the JSON object."""

# ── User profiling ─────────────────────────────────────────────────────────────

USER_PROFILE_PROMPT = """Analyze this Reddit user's activity and generate a 3-4 sentence behavioral summary.

Username: {username}
Account age: {account_age}
Total karma: {total_karma}
Posts analyzed: {posts_count}
Comments analyzed: {comments_count}
Top subreddits: {top_subreddits}
Avg post score: {avg_post_score}
Avg comment score: {avg_comment_score}
Active hours (UTC): {active_hours}
Top post titles (sample): {top_posts}

Write a concise behavioral summary covering:
- Primary interests and communities
- Posting style and engagement level
- Any notable patterns
Keep it to 3-4 sentences, factual and neutral."""

# ── Thread summarization ───────────────────────────────────────────────────────

THREAD_SUMMARY_PROMPT = """Analyze this Reddit thread and provide an intelligence summary.

Post title: {title}
Subreddit: r/{subreddit}
Score: {score} ({upvote_ratio:.0%} upvoted)
Total comments: {total_comments}
Unique participants: {unique_participants}

Top comments (sample):
{top_comments}

Respond in this exact JSON format:
{{
  "summary": "2-3 sentence thread summary",
  "key_arguments": ["argument 1", "argument 2", "argument 3"],
  "consensus": "One sentence describing the community consensus",
  "main_opinions": ["opinion 1", "opinion 2", "opinion 3"],
  "sentiment_breakdown": {{"Positive": 0.35, "Neutral": 0.40, "Negative": 0.25}}
}}"""

# ── Post summarization ────────────────────────────────────────────────────────

POST_SUMMARY_PROMPT = """Summarize this Reddit post and its discussion.

Title: {title}
Subreddit: r/{subreddit}
Score: {score} ({upvote_ratio:.0%} upvoted)
Body: {body}
Top comments:
{top_comments}

Respond in this exact JSON format:
{{
  "summary": "2-3 sentence summary of the post and discussion",
  "key_arguments": ["point 1", "point 2", "point 3"],
  "consensus": "Community consensus in one sentence",
  "sentiment_breakdown": {{"Positive": 0.35, "Neutral": 0.40, "Negative": 0.25}}
}}"""

# ── Comment analysis ──────────────────────────────────────────────────────────

COMMENT_ANALYSIS_PROMPT = """Analyze this Reddit comment.

Comment: {body}
Context: Posted in r/{subreddit} on post "{post_title}"
Parent: {parent_context}

Respond in this exact JSON format:
{{
  "sentiment": "Positive|Neutral|Negative",
  "tone": "Helpful|Aggressive|Sarcastic|Informative|Humorous",
  "topic": "Brief topic classification (1-3 words)",
  "toxicity_score": 0.0,
  "constructiveness_score": 0.0,
  "summary": "One sentence summary of the comment"
}}

toxicity_score and constructiveness_score are floats from 0.0 to 1.0."""

# ── Sentiment batch classification ────────────────────────────────────────────

SENTIMENT_BATCH_PROMPT = """Classify the sentiment of each of these Reddit comments.
Return ONLY a JSON array of objects, one per comment, in order.
Each object: {{"sentiment": "Positive|Neutral|Negative"}}

Comments:
{comments}"""
