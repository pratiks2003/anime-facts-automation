# InstaAgent architecture

InstaAgent publishes anime Reels to Instagram automatically, using only free tiers.

| Plane | Runs on | Responsibility |
|---|---|---|
| **Control** | Salesforce (InstaAgent org) | Decides what to make and when, stores every outcome, hosts the admin site |
| **Execution** | GitHub Actions | Facts → script → art → voice → video → Instagram |

```
 Admin (LWR site, login) ─┐
                          ▼
 Salesforce ── ReelScheduler (hourly :40) ── ReelSlotPlanner ── ReelDispatchQueueable ──► GitHub API
   │  Settings: automation, 2 Reels/day, auto-approve, dry run          (Named Credential GitHub_API)
   │  Format_Rotation__mdt: weekday × slot → format                            │
   │                                                                        reel.yml
   │◄──── REST upserts by external ID (OAuth client credentials, ───── pipeline/run_reel.py
   │      API-only integration user)                                         │
   ▼                                                                         ├─ planner: AniList trends / news
 Anime_Content__c (Reel) · Video_Job__c · Social_Post__c ·                   ├─ ingest: Wikipedia facts, quote-verified
 Anime_Knowledge__c · Anime__c                                               ├─ writer: Cloudflare gpt-oss-120b
                                                                             ├─ video: FLUX art, Kokoro voice, FFmpeg
                                                                             ├─ preview → GitHub release "reels"
                                                                             └─ Instagram Graph API (Unified Login)
```

## Daily flow
1. **Schedule.** `ReelScheduler` runs every hour at :40. When a slot falls within the next 60 minutes, `ReelSlotPlanner` creates the Reel for it.
   - Slots are 09:00 IST, plus 19:00 IST when "2 Reels per day" is on.
   - Each Reel gets a stable ID (`R-yyyyMMdd-AM/PM`), so a slot is never created twice.
   - The format comes from `Format_Rotation__mdt`.
2. **Dispatch.** `ReelDispatchQueueable` starts `reel.yml` with the Reel ID, a job correlation ID, the format and the switches.
3. **Generate.** `pipeline.run_reel generate` reports each stage to Salesforce:
   - **Plan:** choose the anime and facts. News formats use live AniList data.
   - **Ingest:** new Wikipedia facts are kept only if their evidence quote is found in the page text.
   - **Write:** the LLM writes the script, which is checked in code (fact citations, labels, lengths, 5 hashtags).
   - **Render** and run the Instagram spec checks.
   - **Upload** the cover and video preview.
4. **Publish.**

   | Auto-approve | Dry run | Result |
   |---|---|---|
   | On | Off | Publish to Instagram, record a Social Post, set the Reel to Published |
   | On | On | Stop at Rendered and Auto-Approved; nothing is posted |
   | Off | Either | Wait for approval in the admin site; approving runs `publish.yml` |
5. **Clean up.** Jobs with no callback for 75 minutes are marked Timed Out and the Reel Failed.

## Safety rules
- **Facts:** only verified facts are written into scripts. A Reel is publishable only if every cited fact ID exists and is verified.
- **Labels:** theory and what-if scenes are labeled on screen.
- **Secrets:**
  - The GitHub token lives in a Salesforce External Credential.
  - Instagram, Cloudflare and Salesforce credentials live in GitHub Secrets.
  - Nothing secret is committed.
- **Least privilege:** GitHub authenticates as an API-only integration user, through the `InstaAgent_Integration` permission set, with no delete access.
- **Admin site:** it requires login and runs as the logged-in user (`WITH USER_MODE`, `as user`).
- **Cost:** on the Cloudflare free plan, requests beyond the daily allowance are refused rather than billed. If the allowance runs out during art generation, the Reel uses licensed photos instead.

## Free-tier budget (per day)
| Resource | Use | Limit |
|---|---|---|
| Cloudflare Workers AI | ~3,300 Neurons per Reel (art) + ~250 (script) + ~700 when topping up knowledge | 10,000/day |
| GitHub Actions | ~3 min per Reel | unlimited (public repo) |
| Instagram API | 1–2 posts | 100/day |
| Salesforce data storage | ~2 KB per record | 5 MB |

## Repository map
| Path | What |
|---|---|
| `force-app/` | Salesforce metadata: objects, Apex, LWC admin app, LWR site |
| `pipeline/` | Daily job: planner, news, ingest, writer, LLM, release store, orchestrator |
| `video/` | Renderer: script schema, TTS, visuals, image generation, media, SFX, validation |
| `rag/`, `knowledge/` | Fact schema, BM25 retrieval, the knowledge base |
| `integrations/` | Instagram and Salesforce clients |
| `.github/workflows/` | `reel.yml`, `publish.yml`, tests, sample render |


## Agentforce brain (Phase A)
Every Reel is written by Prompt Builder agents running GPT-4.1 through the Einstein Trust Layer, on the free tier of 150 outputs per hour.

| Agent (template) | Job |
|---|---|
| `Insta_Script_Writer` | Writes the script from verified facts only, applying the Performance Analyst's lessons |
| `Insta_Creative_Director` | Scores hook, flow, repetition, payoff order, why-care, pacing, visual variety and call to action (0–10); rewrites until 8.5; writes the production brief (voice, music mood and keywords, SFX) |
| `Insta_Fact_Checker` | Checks each claim against its cited facts; problems go back to the Director for one fix pass |
| `Insta_Caption_Writer` | Writes the caption and 5 real hashtags |
| `Insta_JSON_Task` | Generic JSON task used for Wikipedia fact extraction and verification |
| `Insta_Music_Supervisor` | Picks a track (available, not yet wired into the pipeline) |

- **Chain and endpoint:** `ScriptStudio` runs the chain with at most 7 generations per Reel, exposed at `/services/apexrest/insta/studio/script`.
- **Script source order:** Agentforce → Cloudflare LLM → template. Every source must pass the code rules.
- **Quality gate:** the `Reel_Quality_Gate` Flow holds any auto-approved Reel scoring below `Quality_Threshold__c` (8), or with QA issues, for review.
- **Voice:** a random Deepgram Aura-2 voice per Reel, stable per Reel ID. If Aura is unavailable, the whole narration falls back to Kokoro.
- **Deploying prompts:** `tools/deploy_prompts.py` deploys the templates and activates their latest versions.

## Learning loop (Phase B)
- **Daily (`metrics.yml`, 07:17 IST):** syncs Instagram likes and comments, plus views, reach, watch time, shares and saves once `instagram_manage_insights` is granted. New comments go to the Community Manager agent (`/insta/studio/comments`), which writes reply drafts and topic ideas.
- **Weekly (Mondays 08:00 IST):** the Performance Analyst writes a `Performance_Review__c` (summary, what to feature or avoid, hook lessons) and adds topic ideas.
- **Topic backlog (`Topic_Idea__c`):** fed by the Analyst, comments, public votes and the admin. Each Reel uses the top-voted idea for its format.

## Websites (Phase C)
| Site | URL | Access |
|---|---|---|
| Admin | `/admin` | Login required: dashboard, Reels queue, Published, Ideas & Insights, Settings |
| Public "Senpai Secrets" | `/senpai` | Guests |

The public site shows Latest Reels (Instagram embeds), This Week in Anime (live AniList data, IST times), the Fact Vault (verified, low-spoiler facts with sources) and voting.

- **Guest backend:** `PublicSiteController` is the only class the guest can call (permission set `InstaAgent_Public`). It returns only published, verified or curated data, through explicit field lists.
- **Public suggestions** enter as "Suggested" and reach the backlog only after admin approval.
- **Org-specific files:** the site and network files are kept out of git because they contain org usernames and emails. Recreate a site with `sf community create` and the steps in this repo's history.
