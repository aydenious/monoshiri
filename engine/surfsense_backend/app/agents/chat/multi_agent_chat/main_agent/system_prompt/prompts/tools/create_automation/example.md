<example>
user: "Every weekday at 9am, summarize new documents in the Research folder and save the summary as a note."
→ create_automation(intent="Every weekday at 09:00 UTC, summarize documents added to the Research folder since the last run, then save the summary as a note at /documents/Research/daily-digest.md. Static inputs: folder_path='/documents/Research'.")
tool returns: {"status": "saved", "automation_id": 42, "name": "Daily Research digest"}
(Reply briefly: "Saved as automation #42 — runs weekdays at 9am UTC.")
</example>

<example>
user: "Once a week on Mondays at 7am Paris time, recap last week's crawled web pages under the Projects folder."
→ create_automation(intent="Every Monday at 07:00 Europe/Paris, read the web pages crawled during the previous week, then save the recap as a note under the Projects folder. Static inputs: folder_path='/documents/Projects'. The user did NOT specify the note's file name — leave note_path as a placeholder.")
tool returns: {"status": "saved", "automation_id": 51, "name": "Weekly web recap"}
(Reply: "Saved as automation #51. I left the note file name as a placeholder — set it on the automation before next Monday.")
</example>
