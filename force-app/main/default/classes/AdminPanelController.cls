/**
 * Backend for the InstaAgent admin site (LWR). All queries/DML run in user mode so the
 * site can never see or change more than the logged-in admin's permissions allow.
 */
public with sharing class AdminPanelController {
    private static final Set<String> RETRYABLE = new Set<String>{ 'Failed', 'Rejected', 'Cancelled', 'Planned' };

    @AuraEnabled
    public static Map<String, Object> getOverview() {
        try {
            InstaAgent_Settings__c s = InstaAgentConfig.get();
            Map<String, Integer> counts = new Map<String, Integer>();
            for (AggregateResult ar : [SELECT Status__c st, COUNT(Id) n FROM Anime_Content__c WITH USER_MODE GROUP BY Status__c]) {
                counts.put((String) ar.get('st'), (Integer) ar.get('n'));
            }
            List<CronTrigger> cron = [
                SELECT NextFireTime, State
                FROM CronTrigger
                WHERE CronJobDetail.Name = :ReelScheduler.JOB_NAME
                LIMIT 1
            ];
            return new Map<String, Object>{
                'settings' => settingsMap(s),
                'counts' => counts,
                'held' => [SELECT COUNT() FROM Anime_Content__c WHERE Status__c = 'Rendered' AND Approval_Status__c = 'Pending' WITH USER_MODE],
                'upcoming' => upcomingSlots(Datetime.now(), s, 4),
                'reels' => listReels(null, 12),
                'jobs' => [
                    SELECT Id, Name, Job_Type__c, Status__c, Stage__c, Run_URL__c, Error_Message__c, Dispatched_At__c, Completed_At__c, Content__r.Name
                    FROM Video_Job__c
                    WITH USER_MODE
                    ORDER BY CreatedDate DESC
                    LIMIT 10
                ],
                'posts' => listPosts(6),
                'scheduler' => cron.isEmpty()
                    ? new Map<String, Object>{ 'running' => false }
                    : new Map<String, Object>{ 'running' => true, 'nextRun' => cron[0].NextFireTime, 'state' => cron[0].State }
            };
        } catch (Exception e) {
            throw auraError(e);
        }
    }

    @AuraEnabled
    public static Map<String, Object> saveSettings(Map<String, Object> values) {
        try {
            InstaAgentConfig.save(
                new InstaAgent_Settings__c(
                    Automation_Enabled__c = (Boolean) values.get('automationEnabled'),
                    Two_Reels_Per_Day__c = (Boolean) values.get('twoReelsPerDay'),
                    Auto_Approve__c = (Boolean) values.get('autoApprove'),
                    Dry_Run__c = (Boolean) values.get('dryRun'),
                    Morning_Slot_IST__c = (String) values.get('morningSlot'),
                    Evening_Slot_IST__c = (String) values.get('eveningSlot'),
                    Trial_Reels_Enabled__c = values.get('trialReels') == true,
                    Auto_Reply_Comments__c = values.get('autoReply') != false,
                    Watchlist__c = values.get('watchlist') == null ? null : String.valueOf(values.get('watchlist'))
                )
            );
            if ((Boolean) values.get('automationEnabled')) {
                ensureScheduler();
            }
            return settingsMap(InstaAgentConfig.get());
        } catch (Exception e) {
            throw auraError(e);
        }
    }

    @AuraEnabled
    public static List<Anime_Content__c> listReels(String status, Integer limitSize) {
        Integer n = limitSize == null ? 25 : Math.min(limitSize, 200);
        String filter = String.isBlank(status) ? '' : ' AND Status__c = :status';
        return Database.query(
            'SELECT Id, Name, External_Id__c, Format__c, Slot__c, Status__c, Approval_Status__c, Scheduled_For__c, Topic__c, Hook__c, ' +
                'Caption__c, Hashtags__c, Cover_URL__c, Video_URL__c, Duration_Seconds__c, Dry_Run__c, Error_Message__c, Anime__r.Name, ' +
                'Quality_Score__c, Director_Notes__c, Production_Brief__c, QA_Issues__c, Hold_Reason__c, Script_Source__c, Music_Choice__c, Music_Rating__c, Style__r.Name, Strategy__c, Needs_Trending_Sound__c, ' +
                '(SELECT Permalink__c, Instagram_Media_Id__c, Views__c, Likes__c FROM Social_Posts__r ORDER BY CreatedDate DESC LIMIT 1) ' +
                'FROM Anime_Content__c WHERE Id != null' +
                filter +
                ' WITH USER_MODE ORDER BY Scheduled_For__c DESC NULLS LAST, CreatedDate DESC LIMIT :n'
        );
    }

    @AuraEnabled
    public static List<Social_Post__c> listPosts(Integer limitSize) {
        Integer n = limitSize == null ? 25 : Math.min(limitSize, 200);
        return [
            SELECT Id, Name, Permalink__c, Instagram_Media_Id__c, Published_At__c, Status__c, Views__c, Reach__c, Likes__c,
                Comments__c, Shares__c, Saves__c, Avg_Watch_Time_Ms__c, Metrics_Updated_At__c, Content__r.Name, Content__r.Cover_URL__c,
                Content__r.Format__c
            FROM Social_Post__c
            WITH USER_MODE
            ORDER BY Published_At__c DESC NULLS LAST
            LIMIT :n
        ];
    }

    @AuraEnabled
    public static void approveReel(Id reelId) {
        setApproval(reelId, 'Approved');
    }

    @AuraEnabled
    public static void rejectReel(Id reelId) {
        setApproval(reelId, 'Rejected');
    }

    /** Re-run generation for a failed/rejected Reel. */
    @AuraEnabled
    public static void retryReel(Id reelId) {
        try {
            Anime_Content__c c = [SELECT Id, Status__c FROM Anime_Content__c WHERE Id = :reelId WITH USER_MODE];
            if (!RETRYABLE.contains(c.Status__c)) {
                throw new InstaAgentConfig.InstaAgentException('Only failed, rejected or cancelled Reels can be retried (status: ' + c.Status__c + ')');
            }
            c.Status__c = 'Queued';
            c.Approval_Status__c = 'Pending';
            c.Error_Message__c = null;
            update as user c;
            System.enqueueJob(new ReelDispatchQueueable(new List<Id>{ c.Id }, ReelDispatchQueueable.GENERATE));
        } catch (Exception e) {
            throw auraError(e);
        }
    }

    /** Make a Reel right now, outside the schedule. */
    @AuraEnabled
    public static Id createReelNow(String format, String topic) {
        try {
            InstaAgent_Settings__c s = InstaAgentConfig.get();
            Datetime now = Datetime.now();
            Anime_Content__c c = new Anime_Content__c(
                Name = (String.isBlank(format) ? 'Fact' : format) + ' · ' + now.format('d MMM HH:mm', InstaAgentConfig.TIMEZONE_ID) + ' Manual',
                External_Id__c = 'R-' + now.format('yyyyMMdd-HHmmss', InstaAgentConfig.TIMEZONE_ID) + '-MAN',
                Format__c = String.isBlank(format) ? 'Fact' : format,
                Topic__c = topic,
                Slot__c = 'Manual',
                Scheduled_For__c = now,
                Status__c = 'Queued',
                Approval_Status__c = 'Pending',
                Dry_Run__c = s.Dry_Run__c
            );
            insert as user c;
            System.enqueueJob(new ReelDispatchQueueable(new List<Id>{ c.Id }, ReelDispatchQueueable.GENERATE));
            return c.Id;
        } catch (Exception e) {
            throw auraError(e);
        }
    }

    @AuraEnabled
    public static Map<String, Object> getIdeas() {
        try {
            return new Map<String, Object>{
                'ideas' => [
                    SELECT Id, Name, Anime__c, Format__c, Source__c, Votes__c, Status__c, CreatedDate
                    FROM Topic_Idea__c
                    WHERE Status__c IN ('Suggested', 'New')
                    WITH USER_MODE
                    ORDER BY Status__c DESC, Votes__c DESC NULLS LAST, CreatedDate DESC
                    LIMIT 100
                ],
                'review' => [
                    SELECT Name, Period_End__c, Summary__c, Feature_Anime__c, Avoid_Anime__c, Hook_Lessons__c, Reels_Analysed__c
                    FROM Performance_Review__c
                    WITH USER_MODE
                    ORDER BY CreatedDate DESC
                    LIMIT 1
                ]
            };
        } catch (Exception e) {
            throw auraError(e);
        }
    }

    @AuraEnabled
    public static List<Comment_Reply__c> listComments(Integer limitSize) {
        Integer n = limitSize == null ? 50 : Math.min(limitSize, 200);
        return [
            SELECT Id, Name, Comment_Text__c, Reply_Text__c, Category__c, Status__c, Posted_At__c, Error_Message__c, CreatedDate,
                Social_Post__r.Permalink__c, Social_Post__r.Content__r.Name
            FROM Comment_Reply__c
            WITH USER_MODE
            ORDER BY CreatedDate DESC
            LIMIT :n
        ];
    }

    @AuraEnabled
    public static void rateMusic(Id reelId, String rating) {
        try {
            if (rating != 'Good' && rating != 'Bad') {
                throw new InstaAgentConfig.InstaAgentException('Rating must be Good or Bad');
            }
            update as user new Anime_Content__c(Id = reelId, Music_Rating__c = rating);
        } catch (Exception e) {
            throw auraError(e);
        }
    }

    /** Approve a public suggestion into the backlog (New) or reject it. */
    @AuraEnabled
    public static void setIdeaStatus(Id ideaId, String status) {
        try {
            if (status != 'New' && status != 'Rejected') {
                throw new InstaAgentConfig.InstaAgentException('Status must be New or Rejected');
            }
            update as user new Topic_Idea__c(Id = ideaId, Status__c = status);
        } catch (Exception e) {
            throw auraError(e);
        }
    }

    // ------------------------------------------------------------------ research: learn, styles, trends

    /** "Learn from a Reel": queue a public Reel link for download + analysis on GitHub. */
    @AuraEnabled
    public static Id addReference(String url, String notes) {
        try {
            if (!ReferenceReels.validUrl(url)) {
                throw new InstaAgentConfig.InstaAgentException('Paste a public Instagram Reel, YouTube Shorts or TikTok link.');
            }
            Reference_Reel__c r = new Reference_Reel__c(Name = url.trim().abbreviate(80), URL__c = url.trim(),
                Notes__c = notes == null ? null : notes.abbreviate(2000), Status__c = 'Queued');
            insert as user r;
            return r.Id;
        } catch (Exception e) {
            throw auraError(e);
        }
    }

    @AuraEnabled
    public static Map<String, Object> getResearch() {
        try {
            return new Map<String, Object>{
                'references' => [
                    SELECT Id, Name, URL__c, Status__c, Notes__c, Lessons__c, Error__c, Analyzed_At__c, Style__r.Name, CreatedDate
                    FROM Reference_Reel__c
                    WITH USER_MODE
                    ORDER BY CreatedDate DESC
                    LIMIT 30
                ],
                'styles' => [
                    SELECT Id, Name, Status__c, Origin__c, Description__c, Reels__c, Avg_Views__c, Avg_Watch_Seconds__c,
                        Performance_Score__c, Last_Used__c
                    FROM Style__c
                    WITH USER_MODE
                    ORDER BY Status__c, Performance_Score__c DESC NULLS LAST
                    LIMIT 50
                ],
                'brief' => [
                    SELECT Name, Brief_Date__c, Summary__c, Brief_JSON__c, Needs_Trending_Sound__c
                    FROM Trend_Brief__c
                    WITH USER_MODE
                    ORDER BY Brief_Date__c DESC, CreatedDate DESC
                    LIMIT 1
                ]
            };
        } catch (Exception e) {
            throw auraError(e);
        }
    }

    /** Admin override of the style lifecycle (Active / Testing / Retired). */
    @AuraEnabled
    public static void setStyleStatus(Id styleId, String status) {
        try {
            if (status != 'Active' && status != 'Testing' && status != 'Retired') {
                throw new InstaAgentConfig.InstaAgentException('Status must be Active, Testing or Retired');
            }
            update as user new Style__c(Id = styleId, Status__c = status);
        } catch (Exception e) {
            throw auraError(e);
        }
    }

    @AuraEnabled
    public static List<String> getFormats() {
        List<String> values = new List<String>();
        for (Schema.PicklistEntry e : Anime_Content__c.Format__c.getDescribe().getPicklistValues()) {
            values.add(e.getValue());
        }
        return values;
    }

    // ------------------------------------------------------------------ helpers

    @TestVisible
    private static List<Map<String, Object>> upcomingSlots(Datetime now, InstaAgent_Settings__c s, Integer howMany) {
        Map<String, String> rotation = ReelSlotPlanner.rotation();
        List<Map<String, Object>> result = new List<Map<String, Object>>();
        Date todayIst = Date.valueOf(now.format('yyyy-MM-dd', InstaAgentConfig.TIMEZONE_ID));
        for (Integer d = 0; d < 4 && result.size() < howMany; d++) {
            Date day = todayIst.addDays(d);
            List<String> slots = s.Two_Reels_Per_Day__c
                ? new List<String>{ ReelSlotPlanner.MORNING, ReelSlotPlanner.EVENING }
                : new List<String>{ ReelSlotPlanner.MORNING };
            for (String slot : slots) {
                Datetime at = ReelSlotPlanner.istToGmt(day, slot == ReelSlotPlanner.MORNING ? s.Morning_Slot_IST__c : s.Evening_Slot_IST__c);
                if (at > now && result.size() < howMany) {
                    result.add(
                        new Map<String, Object>{
                            'slot' => slot,
                            'at' => at,
                            'format' => rotation.get(at.format('EEE', InstaAgentConfig.TIMEZONE_ID) + '|' + slot)
                        }
                    );
                }
            }
        }
        return result;
    }

    private static void setApproval(Id reelId, String value) {
        try {
            Anime_Content__c c = [SELECT Id, Status__c FROM Anime_Content__c WHERE Id = :reelId WITH USER_MODE];
            if (c.Status__c != 'Rendered') {
                throw new InstaAgentConfig.InstaAgentException('Only rendered Reels can be approved or rejected (status: ' + c.Status__c + ')');
            }
            c.Approval_Status__c = value;
            if (value == 'Rejected') {
                c.Status__c = 'Rejected';
            }
            update as user c; // approval -> trigger dispatches the publish workflow
        } catch (Exception e) {
            throw auraError(e);
        }
    }

    private static void ensureScheduler() {
        if ([SELECT COUNT() FROM CronTrigger WHERE CronJobDetail.Name = :ReelScheduler.JOB_NAME] == 0) {
            ReelScheduler.scheduleHourly();
        }
    }

    private static Map<String, Object> settingsMap(InstaAgent_Settings__c s) {
        return new Map<String, Object>{
            'automationEnabled' => s.Automation_Enabled__c,
            'twoReelsPerDay' => s.Two_Reels_Per_Day__c,
            'autoApprove' => s.Auto_Approve__c,
            'dryRun' => s.Dry_Run__c,
            'morningSlot' => s.Morning_Slot_IST__c,
            'eveningSlot' => s.Evening_Slot_IST__c,
            'repository' => s.GitHub_Repository__c,
            'trialReels' => s.Trial_Reels_Enabled__c,
            'autoReply' => s.Auto_Reply_Comments__c,
            'watchlist' => s.Watchlist__c
        };
    }

    private static AuraHandledException auraError(Exception e) {
        if (e instanceof AuraHandledException) {
            return (AuraHandledException) e;
        }
        AuraHandledException ex = new AuraHandledException(e.getMessage());
        ex.setMessage(e.getMessage());
        return ex;
    }
}
