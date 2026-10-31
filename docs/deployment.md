# Deployment

## Local use

Local installation is the supported option for durable SQLite data and works without any hosted service:

```powershell
.\setup.ps1
.\run.ps1
```

Keep `.env` and `incident_investigator.db` on a trusted machine. Back up the SQLite file using a consistent file copy while the app is stopped.

## Streamlit Community Cloud

As of the official Streamlit documentation checked for this build, Streamlit Community Cloud describes itself as a free GitHub-connected deployment service. It is a reasonable option for a public demonstration, not a durable operational system.

1. Put the project in a GitHub repository.
2. In Streamlit Community Cloud, choose **New app**, select the repository/branch, and set the main file to `app.py`.
3. Configure Python 3.11+ in advanced app settings if needed; Community Cloud supports currently security-supported Python releases.
4. In app settings, add secrets (TOML format):

   ```toml
   GEMINI_API_KEY = "your-key"
   GEMINI_MODEL = "gemini-3.8-flash"
   DATABASE_URL = "sqlite:///incident_investigator.db"
   ```

5. Deploy and verify the UI. Do not commit the secret file.

GitHub import is deliberately limited to at most 10 recent commits and 10 issues. Reading commit file/change statistics requires detail calls, so one import can make up to 12 unauthenticated GitHub API requests. The public API is free to use but rate-limited; set `GITHUB_TOKEN` in Streamlit secrets only if needed for a higher GitHub quota.

### Important hosted-storage limitation

This project uses a local SQLite file. Streamlit Community Cloud's app filesystem should not be assumed persistent across rebuilds/restarts, and the free Community Cloud deployment is not a managed persistent database. A hosted demo may lose incident and review data. Do not use it as an incident system of record. The deployment can also sleep or have resource limits; check the current Community Cloud status and limitations documentation.

### Gemini free-tier caution

Free-tier availability is limited to selected models and quotas vary dynamically. Google currently documents free input/output for selected models, but limits and model availability can change. Check AI Studio's current active quota. Free-tier submitted content may be used to improve Google products according to the current pricing page. Never deploy production incident data unless policy and terms allow it.

Official references:

- [Streamlit Community Cloud](https://docs.streamlit.io/deploy/streamlit-community-cloud)
- [Community Cloud status and limitations](https://docs.streamlit.io/deploy/streamlit-community-cloud/status)
- [Deploy a Streamlit app](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app)
- [Gemini API rate limits](https://ai.google.dev/gemini-api/docs/rate-limits)
- [Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing)

## If durable multi-user hosting is required

Use a deployment environment with persistent storage and an approved database. That changes the MVP's deliberately local SQLite design and should be planned separately; no such paid or cloud infrastructure is required for local operation.
