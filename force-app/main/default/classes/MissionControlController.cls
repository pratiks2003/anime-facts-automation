/**
 * Mission Control: the owner's single screen. One call returns everything (autopilot status, what needs the owner,
 * Reels with their verdicts, the agents' decisions and strategy, trends, health). Every section is independent:
 * if one fails it comes back empty and the page still renders.
 */
public with sharing class MissionControlController {
    private static final Set<String> ACTIVE = new Set<String>{ 'Queued', 'Generating', 'Rendered', 'Approved', 'Publishing' };

    @AuraEnabled
    public static Map<String, Object> getOverview() {
        Map<String, Object> out = new Map<String, Object>{ 'generatedAt' => Datetime.now() };
        Datetime now = Datetime.now();
        try { out.put('autopilot', autopilot(now)); } catch (Exception e) { out.put('autopilot', new Map<String, Object>()); }
        try { out.put('inbox', inbox()); } catch (Exception e) { out.put('inbox', new List<Object>()); }
        try { out.put('reels', reels()); } catch (Exception e) { out.put('reels', new List<Object>()); }
        try { out.put('kpis', kpis(now)); } catch (Exception e) { out.put('kpis', new Map<String, Object>()); }
        try { out.put('feed', feed()); } catch (Exception e) { out.put('feed', new List<Object>()); }
        try { out.put('playbook', playbook()); } catch (Exception e) { out.put('playbook', new List<Object>()); }
        try { out.put('changes', changes()); } catch (Exception e) { out.put('changes', new List<Object>()); }
        try { out.put('requests', requests()); } catch (Exception e) { out.put('requests', new List<Object>()); }
        try { out.put('trends', trends()); } catch (Exception e) { out.put('trends', new List<Object>()); }
        try { out.put('health', health(now)); } catch (Exception e) { out.put('health', new Map<String, Object>()); }
        return out;
    }

    @AuraEnabled
    public static void resolveItem(Id activityId) {
        update new Agent_Activity__c(Id = activityId, Resolved__c = true);
    }

    @AuraEnabled
    public static String askShowrunner(String question) {
        if (String.isBlank(question)) {
            return '';
        }
        try {
            return ShowrunnerEvents.ask(question.abbreviate(2000));
        } catch (Exception e) {
            throw new AuraHandledException('The Showrunner could not answer right now: ' + e.getMessage());
        }
    }

    // ---------------------------------------------------------------- sections

    @TestVisible
    private static Map<String, Object> autopilot(Datetime now) {
        InstaAgent_Settings__c s = InstaAgentConfig.get();
        Map<String, Object> a = new Map<String, Object>{
            'automationEnabled' => s.Automation_Enabled__c == true,
            'dryRun' => s.Dry_Run__c == true,
            'reelsPerDay' => s.Two_Reels_Per_Day__c != true ? 1 : (s.Three_Reels_Per_Day__c == true ? 3 : 2)
        };
        Datetime next;
        String nextName;
        Map<String, String> slots = ReelSlotPlanner.enabledSlots(s);
        List<Map<String, Object>> schedule = new List<Map<String, Object>>();
        Date today = Date.valueOf(now.format('yyyy-MM-dd', InstaAgentConfig.TIMEZONE_ID));
        for (Integer d = 0; d < 2; d++) {
            for (String name : slots.keySet()) {
                if (String.isBlank(slots.get(name))) continue;
                Datetime at = ReelSlotPlanner.istToGmt(today.addDays(d), slots.get(name));
                if (d == 0) schedule.add(new Map<String, Object>{ 'name' => name, 'time' => slots.get(name), 'at' => at, 'done' => at < now });
                if (at > now && (next == null || at < next)) {
                    next = at;
                    nextName = name;
                }
            }
        }
        a.put('nextSlot', nextName);
        a.put('nextSlotAt', next);
        a.put('schedule', schedule);
        List<Anime_Content__c> active = [
            SELECT Id, External_Id__c, Slot__c, Status__c, Topic__c, Format__c, Anime__r.Name, Script_JSON__c, Showrunner_Directive__c, Cover_URL__c,
                (SELECT Stage__c, Status__c, Run_URL__c, CreatedDate FROM Video_Jobs__r ORDER BY CreatedDate DESC LIMIT 1)
            FROM Anime_Content__c WHERE Dry_Run__c = false AND CreatedDate = LAST_N_DAYS:2
                AND (Status__c IN ('Queued', 'Generating', 'Publishing')
                     OR (Status__c = 'Rendered' AND Approval_Status__c = 'Pending' AND Hold_Reason__c != null))
            ORDER BY CreatedDate DESC LIMIT 1
        ];
        if (!active.isEmpty()) {
            Anime_Content__c c = active[0];
            Video_Job__c job = c.Video_Jobs__r.isEmpty() ? null : c.Video_Jobs__r[0];
            a.put('active', new Map<String, Object>{
                'id' => c.External_Id__c, 'slot' => c.Slot__c, 'status' => c.Status__c, 'topic' => c.Topic__c,
                'format' => c.Format__c, 'show' => AdminAppController.showOf(c, ShowrunnerSupport.parse(c.Showrunner_Directive__c)),
                'stage' => job == null ? null : job.Stage__c, 'jobStatus' => job == null ? null : job.Status__c,
                'runUrl' => job == null ? null : job.Run_URL__c, 'cover' => c.Cover_URL__c,
                'step' => step(c.Status__c, job == null ? null : job.Stage__c)
            });
        }
        return a;
    }

    /** 0 Planned, 1 Rendering, 2 Quality check, 3 Posting, 4 Live. */
    @TestVisible
    private static Integer step(String status, String stage) {
        String st = stage == null ? '' : stage.toLowerCase();
        if (status == 'Published') return 4;
        if (status == 'Publishing' || status == 'Approved' || st.contains('publish') || st.contains('post spacing')) return 3;
        if (status == 'Rendered' || st.contains('quality') || st.contains('review') || st.contains('qa')) return 2;
        if (status == 'Generating' || st.contains('render') || st.contains('writ') || st.contains('research') || st.contains('script')) return 1;
        return 0;
    }

    private static List<Map<String, Object>> inbox() {
        List<Map<String, Object>> rows = new List<Map<String, Object>>();
        for (Agent_Activity__c a : [
            SELECT Id, Agent__c, Action__c, Reel__c, Details__c, Severity__c, CreatedDate
            FROM Agent_Activity__c WHERE Needs_Owner__c = true AND Resolved__c = false ORDER BY CreatedDate DESC LIMIT 20
        ]) {
            rows.add(activity(a));
        }
        return rows;
    }

    private static List<Map<String, Object>> reels() {
        List<Map<String, Object>> rows = new List<Map<String, Object>>();
        for (Anime_Content__c c : [
            SELECT Id, External_Id__c, Slot__c, Anime__r.Name, Script_JSON__c, Format__c, Topic__c, Status__c, Performance_Index__c, Verdict__c,
                Scored_At__c, Remakes__c, Showrunner_Directive__c, Render_Report__c, Cover_URL__c, Video_URL__c, Quality_Score__c,
                Scheduled_For__c, CreatedDate,
                (SELECT Views__c, Reach__c, Shares__c, Saves__c, Likes__c, Comments__c, Avg_Watch_Time_Ms__c, Permalink__c,
                        Published_At__c FROM Social_Posts__r ORDER BY CreatedDate DESC LIMIT 1)
            FROM Anime_Content__c ORDER BY CreatedDate DESC LIMIT 12
        ]) {
            Map<String, Object> directive = asMap(ShowrunnerSupport.parse(c.Showrunner_Directive__c));
            Map<String, Object> report = asMap(ShowrunnerSupport.parse(c.Render_Report__c));
            Map<String, Object> visual = asMap(report.get('visual_qa'));
            Social_Post__c p = c.Social_Posts__r.isEmpty() ? null : c.Social_Posts__r[0];
            rows.add(new Map<String, Object>{
                'id' => c.External_Id__c, 'recordId' => c.Id, 'slot' => c.Slot__c,
                'show' => AdminAppController.showOf(c, directive),
                'format' => c.Format__c, 'topic' => c.Topic__c, 'status' => c.Status__c,
                'index' => c.Performance_Index__c, 'verdict' => c.Verdict__c, 'scoredAt' => c.Scored_At__c,
                'remakes' => c.Remakes__c, 'cover' => c.Cover_URL__c, 'video' => c.Video_URL__c,
                'scheduledFor' => c.Scheduled_For__c, 'createdDate' => c.CreatedDate,
                'plannedBy' => directive.get('by'), 'brief' => directive.get('notes') == null ? null : String.valueOf(directive.get('notes')).abbreviate(400),
                'effects' => effects(directive.get('edits'), report.get('effects_used')),
                'style' => directive.get('style'),
                'editScore' => visual.get('edit_score'), 'directionScore' => visual.get('direction_score'),
                'directionNotes' => visual.get('direction_notes'), 'qualityScore' => c.Quality_Score__c,
                'views' => p == null ? null : p.Views__c, 'reach' => p == null ? null : p.Reach__c,
                'shares' => p == null ? null : p.Shares__c, 'saves' => p == null ? null : p.Saves__c,
                'likes' => p == null ? null : p.Likes__c, 'comments' => p == null ? null : p.Comments__c,
                'watchSeconds' => p == null || p.Avg_Watch_Time_Ms__c == null ? null : (p.Avg_Watch_Time_Ms__c / 1000).setScale(1),
                'permalink' => p == null ? null : p.Permalink__c, 'publishedAt' => p == null ? null : p.Published_At__c
            });
        }
        return rows;
    }

    private static Map<String, Object> kpis(Datetime now) {
        Map<String, Object> k = new Map<String, Object>();
        Decimal views = 0, watch = 0;
        Integer watchN = 0;
        for (Social_Post__c p : [SELECT Views__c, Avg_Watch_Time_Ms__c FROM Social_Post__c
                                 WHERE Status__c = 'Published' AND Published_At__c = LAST_N_DAYS:7]) {
            views += p.Views__c == null ? 0 : p.Views__c;
            if (p.Avg_Watch_Time_Ms__c != null) {
                watch += p.Avg_Watch_Time_Ms__c;
                watchN++;
            }
        }
        k.put('views7d', views);
        k.put('avgWatchSeconds', watchN == 0 ? null : (watch / watchN / 1000).setScale(1));
        Integer winners = 0, losers = 0, scored = 0;
        Decimal indexSum = 0;
        for (Anime_Content__c c : [SELECT Verdict__c, Performance_Index__c FROM Anime_Content__c
                                   WHERE Performance_Index__c != null AND CreatedDate = LAST_N_DAYS:30]) {
            scored++;
            indexSum += c.Performance_Index__c;
            if (c.Verdict__c == 'Winner') winners++;
            if (c.Verdict__c == 'Loser') losers++;
        }
        k.put('winners', winners);
        k.put('losers', losers);
        k.put('avgIndex', scored == 0 ? null : (indexSum / scored).setScale(2));
        k.put('scored', scored);
        Map<String, Object> policy = InstaAgentConfig.postingPolicy();
        k.put('posts24h', policy.get('posts_last_24h'));
        k.put('maxPerDay', policy.get('max_per_day'));
        k.put('published', [SELECT COUNT() FROM Social_Post__c WHERE Status__c = 'Published']);
        return k;
    }

    private static List<Map<String, Object>> feed() {
        List<Map<String, Object>> rows = new List<Map<String, Object>>();
        for (Agent_Activity__c a : [SELECT Id, Agent__c, Action__c, Reel__c, Details__c, Severity__c, CreatedDate, Needs_Owner__c, Resolved__c
                                    FROM Agent_Activity__c ORDER BY CreatedDate DESC LIMIT 30]) {
            rows.add(activity(a));
        }
        return rows;
    }

    private static List<Map<String, Object>> playbook() {
        List<Map<String, Object>> rows = new List<Map<String, Object>>();
        for (Agent_Playbook__c p : [SELECT Name, Key__c, Value__c, Explanation__c, LastModifiedDate FROM Agent_Playbook__c ORDER BY Key__c LIMIT 50]) {
            rows.add(new Map<String, Object>{ 'name' => p.Name, 'key' => p.Key__c, 'value' => p.Value__c,
                'explanation' => p.Explanation__c, 'updated' => p.LastModifiedDate });
        }
        return rows;
    }

    private static List<Map<String, Object>> changes() {
        List<Map<String, Object>> rows = new List<Map<String, Object>>();
        for (Strategy_Change__c s : [SELECT Id, Setting__c, Old_Value__c, New_Value__c, Reason__c, Baseline_Index__c, Result_Index__c,
                                         Reels_Since__c, Status__c, Changed_By__c, CreatedDate
                                     FROM Strategy_Change__c ORDER BY CreatedDate DESC LIMIT 15]) {
            rows.add(new Map<String, Object>{ 'id' => s.Id, 'setting' => s.Setting__c, 'oldValue' => s.Old_Value__c,
                'newValue' => s.New_Value__c, 'reason' => s.Reason__c, 'baseline' => s.Baseline_Index__c, 'result' => s.Result_Index__c,
                'reelsSince' => s.Reels_Since__c, 'status' => s.Status__c, 'by' => s.Changed_By__c, 'createdDate' => s.CreatedDate });
        }
        return rows;
    }

    private static List<Map<String, Object>> requests() {
        List<Map<String, Object>> rows = new List<Map<String, Object>>();
        for (Agent_Activity__c a : [SELECT Id, Agent__c, Action__c, Reel__c, Details__c, Severity__c, CreatedDate, Needs_Owner__c, Resolved__c
                                    FROM Agent_Activity__c WHERE Action__c IN ('Improvement request', 'Feature request')
                                    ORDER BY CreatedDate DESC LIMIT 15]) {
            rows.add(activity(a));
        }
        return rows;
    }

    private static List<Map<String, Object>> trends() {
        List<Map<String, Object>> rows = new List<Map<String, Object>>();
        List<Trend_Brief__c> briefs = [SELECT Brief_JSON__c FROM Trend_Brief__c ORDER BY CreatedDate DESC LIMIT 1];
        if (briefs.isEmpty()) return rows;
        Object hot = asMap(ShowrunnerSupport.parse(briefs[0].Brief_JSON__c)).get('hot_topics');
        if (hot instanceof List<Object>) {
            for (Object o : (List<Object>) hot) {
                Map<String, Object> t = asMap(o);
                if (t.isEmpty()) continue;
                rows.add(new Map<String, Object>{ 'topic' => t.get('topic'), 'anime' => t.get('anime'), 'heat' => t.get('heat') });
                if (rows.size() == 6) break;
            }
        }
        return rows;
    }

    private static Map<String, Object> health(Datetime now) {
        Map<String, Object> h = new Map<String, Object>();
        List<Social_Post__c> m = [SELECT Metrics_Updated_At__c FROM Social_Post__c WHERE Metrics_Updated_At__c != null
                                  ORDER BY Metrics_Updated_At__c DESC LIMIT 1];
        h.put('metricsUpdatedAt', m.isEmpty() ? null : m[0].Metrics_Updated_At__c);
        h.put('failedJobs24h', [SELECT COUNT() FROM Video_Job__c WHERE Status__c = 'Failed' AND CreatedDate = LAST_N_DAYS:1]);
        List<Trend_Brief__c> b = [SELECT CreatedDate FROM Trend_Brief__c ORDER BY CreatedDate DESC LIMIT 1];
        h.put('trendsUpdatedAt', b.isEmpty() ? null : b[0].CreatedDate);
        h.put('facts', [SELECT COUNT() FROM Anime_Knowledge__c]);
        h.put('lessons', [SELECT COUNT() FROM Agent_Lesson__c WHERE Active__c = true]);
        h.put('openItems', [SELECT COUNT() FROM Agent_Activity__c WHERE Needs_Owner__c = true AND Resolved__c = false]);
        return h;
    }

    // ---------------------------------------------------------------- helpers

    private static Map<String, Object> activity(Agent_Activity__c a) {
        return new Map<String, Object>{ 'id' => a.Id, 'agent' => a.Agent__c, 'action' => a.Action__c, 'reel' => a.Reel__c,
            'details' => a.Details__c == null ? null : a.Details__c.abbreviate(1500), 'severity' => a.Severity__c,
            'createdDate' => a.CreatedDate };
    }

    private static List<String> effects(Object edits, Object used) {
        Set<String> names = new Set<String>();
        if (used instanceof List<Object>) {
            for (Object o : (List<Object>) used) names.add(String.valueOf(o));
        }
        Map<String, Object> byRole = asMap(edits);
        for (String role : byRole.keySet()) {
            Object items = byRole.get(role);
            if (!(items instanceof List<Object>)) continue;
            for (Object item : (List<Object>) items) {
                Object fx = item instanceof Map<String, Object> ? ((Map<String, Object>) item).get('fx') : item;
                if (fx != null) names.add(String.valueOf(fx));
            }
        }
        List<String> out = new List<String>(names);
        out.sort();
        return out;
    }

    private static String showFrom(String directiveJson) {
        Object show = asMap(ShowrunnerSupport.parse(directiveJson)).get('show');
        return show == null ? null : String.valueOf(show);
    }

    private static Map<String, Object> asMap(Object o) {
        return o instanceof Map<String, Object> ? (Map<String, Object>) o : new Map<String, Object>();
    }
}
