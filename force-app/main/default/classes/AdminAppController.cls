/**
 * Admin app (mobile-first shell): the story of one Reel for the live "Agent brain" view, growth evidence and health.
 * Today and Reels reuse MissionControlController.getOverview(). This class only reads data.
 * Every section is isolated: a failing section returns empty and the page still renders.
 */
public with sharing class AdminAppController {
    public static final List<String> STEPS = new List<String>{ 'Plan', 'Write', 'Edit', 'Quality check', 'Decide', 'Post', 'Live on IG' };
    private static final Set<String> ACTIVE = new Set<String>{ 'Queued', 'Generating', 'Rendered', 'Approved', 'Publishing' };

    // ---------------------------------------------------------------- Reel story (live view)

    @AuraEnabled
    public static Map<String, Object> getReelStory(String externalId) {
        Map<String, Object> out = new Map<String, Object>{ 'steps' => STEPS, 'generatedAt' => Datetime.now() };
        List<Anime_Content__c> rows;
        try {
            rows = String.isBlank(externalId) ? defaultReel() : [
                SELECT Id, External_Id__c, Slot__c, Anime__r.Name, Format__c, Topic__c, Status__c, Approval_Status__c, Hold_Reason__c,
                    Performance_Index__c, Verdict__c, Remakes__c, Showrunner_Directive__c, Render_Report__c, Cover_URL__c, Video_URL__c,
                    Music_Choice__c, Scheduled_For__c, CreatedDate, Error_Message__c,
                    (SELECT Views__c, Reach__c, Shares__c, Saves__c, Likes__c, Comments__c, Avg_Watch_Time_Ms__c, Permalink__c, Published_At__c
                     FROM Social_Posts__r ORDER BY CreatedDate DESC LIMIT 1)
                FROM Anime_Content__c WHERE External_Id__c = :externalId LIMIT 1];
        } catch (Exception e) {
            rows = new List<Anime_Content__c>();
        }
        if (rows.isEmpty()) {
            out.put('found', false);
            return out;
        }
        Anime_Content__c c = rows[0];
        out.put('found', true);
        Map<String, Object> directive = asMap(ShowrunnerSupport.parse(c.Showrunner_Directive__c));
        Map<String, Object> report = asMap(ShowrunnerSupport.parse(c.Render_Report__c));
        Social_Post__c p = c.Social_Posts__r.isEmpty() ? null : c.Social_Posts__r[0];
        out.put('reel', new Map<String, Object>{
            'id' => c.External_Id__c, 'slot' => c.Slot__c, 'show' => c.Anime__r == null ? (String) directive.get('show') : c.Anime__r.Name,
            'format' => c.Format__c, 'topic' => c.Topic__c, 'status' => c.Status__c, 'approval' => c.Approval_Status__c,
            'holdReason' => c.Hold_Reason__c, 'index' => c.Performance_Index__c, 'verdict' => c.Verdict__c, 'remakes' => c.Remakes__c,
            'cover' => c.Cover_URL__c, 'video' => c.Video_URL__c, 'music' => c.Music_Choice__c, 'scheduledFor' => c.Scheduled_For__c,
            'createdDate' => c.CreatedDate, 'error' => c.Error_Message__c,
            'views' => p == null ? null : p.Views__c, 'reach' => p == null ? null : p.Reach__c, 'shares' => p == null ? null : p.Shares__c,
            'saves' => p == null ? null : p.Saves__c, 'likes' => p == null ? null : p.Likes__c, 'comments' => p == null ? null : p.Comments__c,
            'watchSeconds' => p == null || p.Avg_Watch_Time_Ms__c == null ? null : (p.Avg_Watch_Time_Ms__c / 1000).setScale(1),
            'permalink' => p == null ? null : p.Permalink__c, 'publishedAt' => p == null ? null : p.Published_At__c
        });
        out.put('plan', directive.isEmpty() ? null : directive);
        out.put('report', report.isEmpty() ? null : report);

        List<Map<String, Object>> events = new List<Map<String, Object>>();
        try {
            for (Agent_Activity__c a : [SELECT Id, Agent__c, Action__c, Details__c, Severity__c, Needs_Owner__c, CreatedDate
                                        FROM Agent_Activity__c WHERE Reel__c = :c.External_Id__c ORDER BY CreatedDate ASC LIMIT 60]) {
                events.add(new Map<String, Object>{ 'source' => 'agent', 't' => a.CreatedDate, 'agent' => a.Agent__c, 'kind' => 'agent',
                    'title' => a.Action__c, 'detail' => a.Details__c, 'severity' => a.Severity__c, 'needsOwner' => a.Needs_Owner__c });
            }
        } catch (Exception e) {
            out.put('agentError', e.getMessage());
        }
        List<Map<String, Object>> jobs = new List<Map<String, Object>>();
        String lastStage;
        Boolean running = false;
        try {
            for (Video_Job__c j : [SELECT Job_Type__c, Status__c, Stage__c, Started_At__c, Completed_At__c, Error_Message__c, Run_URL__c,
                                   Live_Log__c, CreatedDate, Correlation_Id__c FROM Video_Job__c WHERE Content__c = :c.Id ORDER BY CreatedDate ASC LIMIT 20]) {
                jobs.add(new Map<String, Object>{ 'type' => j.Job_Type__c, 'status' => j.Status__c, 'stage' => j.Stage__c,
                    'startedAt' => j.Started_At__c, 'completedAt' => j.Completed_At__c, 'error' => j.Error_Message__c,
                    'runUrl' => j.Run_URL__c, 'createdDate' => j.CreatedDate, 'id' => j.Correlation_Id__c });
                events.add(new Map<String, Object>{ 'source' => 'job', 't' => j.CreatedDate, 'kind' => 'stage',
                    'title' => (j.Job_Type__c == null ? 'Job' : j.Job_Type__c) + ' job ' + (j.Status__c == null ? '' : j.Status__c.toLowerCase()),
                    'detail' => (j.Stage__c == null ? '' : j.Stage__c) + (j.Error_Message__c == null ? '' : '\n' + j.Error_Message__c),
                    'runUrl' => j.Run_URL__c });
                events.addAll(parseLiveLog(j.Live_Log__c));
                if (j.Stage__c != null) lastStage = j.Stage__c;
                if (j.Status__c == 'Running' || j.Status__c == 'Dispatched') running = true;
            }
        } catch (Exception e) {
            out.put('jobError', e.getMessage());
        }
        out.put('jobs', jobs);
        events = sortByTime(events);
        out.put('events', events);
        Boolean inProgress = running || c.Status__c == 'Queued' || c.Status__c == 'Generating' || c.Status__c == 'Publishing'
            || c.Status__c == 'Approved' || (c.Status__c == 'Rendered' && c.Approval_Status__c == 'Pending');
        out.put('inProgress', inProgress);
        out.put('step', step(c.Status__c, c.Approval_Status__c, lastStage, events));
        out.put('stage', lastStage);
        return out;
    }

    private static List<Anime_Content__c> defaultReel() {
        List<Anime_Content__c> active = [
            SELECT Id, External_Id__c, Slot__c, Anime__r.Name, Format__c, Topic__c, Status__c, Approval_Status__c, Hold_Reason__c,
                Performance_Index__c, Verdict__c, Remakes__c, Showrunner_Directive__c, Render_Report__c, Cover_URL__c, Video_URL__c,
                Music_Choice__c, Scheduled_For__c, CreatedDate, Error_Message__c,
                (SELECT Views__c, Reach__c, Shares__c, Saves__c, Likes__c, Comments__c, Avg_Watch_Time_Ms__c, Permalink__c, Published_At__c
                 FROM Social_Posts__r ORDER BY CreatedDate DESC LIMIT 1)
            FROM Anime_Content__c WHERE Status__c IN :ACTIVE AND LastModifiedDate = LAST_N_DAYS:1 ORDER BY LastModifiedDate DESC LIMIT 1];
        if (!active.isEmpty()) return active;
        return [
            SELECT Id, External_Id__c, Slot__c, Anime__r.Name, Format__c, Topic__c, Status__c, Approval_Status__c, Hold_Reason__c,
                Performance_Index__c, Verdict__c, Remakes__c, Showrunner_Directive__c, Render_Report__c, Cover_URL__c, Video_URL__c,
                Music_Choice__c, Scheduled_For__c, CreatedDate, Error_Message__c,
                (SELECT Views__c, Reach__c, Shares__c, Saves__c, Likes__c, Comments__c, Avg_Watch_Time_Ms__c, Permalink__c, Published_At__c
                 FROM Social_Posts__r ORDER BY CreatedDate DESC LIMIT 1)
            FROM Anime_Content__c ORDER BY LastModifiedDate DESC LIMIT 1];
    }

    /** Chips for the reel switcher: newest first. */
    @AuraEnabled
    public static List<Map<String, Object>> listStoryReels() {
        List<Map<String, Object>> out = new List<Map<String, Object>>();
        try {
            for (Anime_Content__c c : [SELECT External_Id__c, Slot__c, Anime__r.Name, Status__c, Verdict__c, Showrunner_Directive__c, CreatedDate
                                       FROM Anime_Content__c ORDER BY CreatedDate DESC LIMIT 15]) {
                Map<String, Object> d = asMap(ShowrunnerSupport.parse(c.Showrunner_Directive__c));
                out.add(new Map<String, Object>{ 'id' => c.External_Id__c, 'slot' => c.Slot__c,
                    'show' => c.Anime__r == null ? (String) d.get('show') : c.Anime__r.Name, 'status' => c.Status__c,
                    'verdict' => c.Verdict__c, 'createdDate' => c.CreatedDate, 'active' => ACTIVE.contains(c.Status__c) });
            }
        } catch (Exception e) {
            return out;
        }
        return out;
    }

    /** 0 Plan, 1 Write, 2 Edit, 3 Quality check, 4 Decide, 5 Post, 6 Live on IG. */
    @TestVisible
    private static Integer step(String status, String approval, String stage, List<Map<String, Object>> events) {
        String st = stage == null ? '' : stage.toLowerCase();
        if (status == 'Published') return 6;
        if (status == 'Publishing' || status == 'Approved' || st.contains('publish') || st.contains('post spacing') || st.contains('posting')) return 5;
        if (status == 'Rendered') return 4;
        Set<String> kinds = new Set<String>();
        for (Map<String, Object> e : events) kinds.add(String.valueOf(e.get('kind')));
        if (st.contains('quality') || st.contains('review') || kinds.contains('qa') || kinds.contains('cover')) return 3;
        if (st.contains('render') || st.contains('upload') || kinds.contains('shots')) return 2;
        if (st.contains('writ') || st.contains('script') || st.contains('research') || kinds.contains('script')) return 1;
        return 0;
    }

    @TestVisible
    private static List<Map<String, Object>> parseLiveLog(String raw) {
        List<Map<String, Object>> out = new List<Map<String, Object>>();
        if (String.isBlank(raw)) return out;
        for (String line : raw.split('\n')) {
            line = line.trim();
            if (!line.startsWith('{')) continue;
            try {
                Map<String, Object> e = (Map<String, Object>) JSON.deserializeUntyped(line);
                Map<String, Object> ev = new Map<String, Object>{ 'source' => 'pipeline', 'kind' => e.get('kind') == null ? 'stage' : e.get('kind'),
                    'title' => e.get('title'), 'detail' => e.get('detail'), 'score' => e.get('score'), 'score2' => e.get('score2') };
                Object t = e.get('t');
                if (t != null) {
                    try {
                        ev.put('t', (Datetime) JSON.deserialize('"' + String.valueOf(t) + '"', Datetime.class));
                    } catch (Exception ignored) {
                        ev.put('t', null);
                    }
                }
                out.add(ev);
            } catch (Exception ignored) {
                continue;
            }
        }
        return out;
    }

    private static List<Map<String, Object>> sortByTime(List<Map<String, Object>> events) {
        // stable insertion sort by time; events without time keep their relative order after the previous timed one
        List<Map<String, Object>> out = new List<Map<String, Object>>();
        Datetime last = Datetime.newInstance(0);
        for (Map<String, Object> e : events) {
            if (e.get('t') == null) e.put('t', last); else last = (Datetime) e.get('t');
        }
        for (Map<String, Object> e : events) {
            Integer i = out.size();
            while (i > 0 && ((Datetime) out[i - 1].get('t')) > (Datetime) e.get('t')) i--;
            if (i == out.size()) out.add(e); else out.add(i, e);
        }
        return out;
    }

    // ---------------------------------------------------------------- Growth

    @AuraEnabled
    public static Map<String, Object> getGrowth() {
        Map<String, Object> out = new Map<String, Object>();
        Datetime now = Datetime.now();
        try { out.put('scoreboard', Scoreboard.insights(now, null)); } catch (Exception e) { out.put('scoreboard', new Map<String, Object>()); }
        try {
            Map<String, Object> values = Playbook.getAll();
            Map<String, String> what = Playbook.explanations();
            List<Map<String, Object>> rows = new List<Map<String, Object>>();
            for (String k : values.keySet()) {
                rows.add(new Map<String, Object>{ 'key' => k, 'value' => JSON.serialize(values.get(k)), 'explanation' => what.get(k) });
            }
            out.put('playbook', rows);
            out.put('data', Playbook.data());
        } catch (Exception e) {
            out.put('playbook', new List<Object>());
            out.put('data', new Map<String, Object>());
        }
        try {
            List<Map<String, Object>> changes = new List<Map<String, Object>>();
            for (Strategy_Change__c c : [SELECT Setting__c, Old_Value__c, New_Value__c, Reason__c, Status__c, Baseline_Index__c, Result_Index__c,
                                         Reels_Since__c, Changed_By__c, CreatedDate FROM Strategy_Change__c ORDER BY CreatedDate DESC LIMIT 20]) {
                changes.add(new Map<String, Object>{ 'setting' => c.Setting__c, 'from' => c.Old_Value__c, 'to' => c.New_Value__c,
                    'why' => c.Reason__c, 'status' => c.Status__c, 'baseline' => c.Baseline_Index__c, 'result' => c.Result_Index__c,
                    'reelsSince' => c.Reels_Since__c, 'by' => c.Changed_By__c, 'when' => c.CreatedDate });
            }
            out.put('changes', changes);
        } catch (Exception e) {
            out.put('changes', new List<Object>());
        }
        try {
            List<Map<String, Object>> reqs = new List<Map<String, Object>>();
            for (Agent_Activity__c a : [SELECT Id, Name, Agent__c, Action__c, Details__c, Reel__c, Resolved__c, CreatedDate FROM Agent_Activity__c
                                        WHERE Action__c IN ('Improvement request', 'Feature request') ORDER BY CreatedDate DESC LIMIT 20]) {
                reqs.add(new Map<String, Object>{ 'id' => a.Id, 'title' => a.Name, 'agent' => a.Agent__c, 'type' => a.Action__c,
                    'detail' => a.Details__c, 'reel' => a.Reel__c, 'resolved' => a.Resolved__c, 'when' => a.CreatedDate });
            }
            out.put('requests', reqs);
        } catch (Exception e) {
            out.put('requests', new List<Object>());
        }
        try {
            List<Agent_Activity__c> rv = [SELECT Details__c, CreatedDate FROM Agent_Activity__c WHERE Action__c = 'Daily review'
                                          ORDER BY CreatedDate DESC LIMIT 1];
            out.put('dailyReview', rv.isEmpty() ? null : new Map<String, Object>{ 'when' => rv[0].CreatedDate,
                'text' => String.valueOf(rv[0].Details__c).substringAfter('Showrunner: ') });
        } catch (Exception e) {
            out.put('dailyReview', null);
        }
        try {
            List<Trend_Brief__c> b = [SELECT Brief_JSON__c, CreatedDate FROM Trend_Brief__c ORDER BY CreatedDate DESC LIMIT 1];
            List<Object> hot = new List<Object>();
            if (!b.isEmpty()) {
                Object topics = asMap(ShowrunnerSupport.parse(b[0].Brief_JSON__c)).get('hot_topics');
                if (topics instanceof List<Object>) {
                    for (Object t : (List<Object>) topics) {
                        if (hot.size() < 8) hot.add(t);
                    }
                }
                out.put('trendsAt', b[0].CreatedDate);
            }
            out.put('trends', hot);
        } catch (Exception e) {
            out.put('trends', new List<Object>());
        }
        return out;
    }

    // ---------------------------------------------------------------- Health

    @AuraEnabled
    public static Map<String, Object> getHealth() {
        Map<String, Object> out = new Map<String, Object>();
        try { out.put('report', ShowrunnerSystemHealth.report(Datetime.now())); } catch (Exception e) { out.put('report', new Map<String, Object>()); }
        try { out.put('posting', InstaAgentConfig.postingPolicy()); } catch (Exception e) { out.put('posting', new Map<String, Object>()); }
        return out;
    }

    private static Map<String, Object> asMap(Object o) {
        return o instanceof Map<String, Object> ? (Map<String, Object>) o : new Map<String, Object>();
    }
}
