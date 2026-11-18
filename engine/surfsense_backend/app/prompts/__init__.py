TITLE_GENERATION_PROMPT = """Generate a concise, descriptive title for the following user query.

<rules>
- The title MUST be between 1 and 6 words
- The title MUST be on a single line
- Capture the main topic or intent of the query
- Do NOT use quotes, punctuation, or formatting
- Do NOT include words like "Chat about" or "Discussion of"
- Return ONLY the title, nothing else
</rules>

<user_query>
{user_query}
</user_query>

Title:"""
