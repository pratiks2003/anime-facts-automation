/** Showrunner action: is the machine healthy and are its data fresh? (for the daily self-audit) */
public with sharing class ShowrunnerSystemHealth {
    public class Request {
        @InvocableVariable(label='Scope' description='Optional, ignored (use all)')
        public String scope;
    }
    public class Result {
        @InvocableVariable(label='Health' description='Health report as JSON')
        public String healthJson;
    }

    @InvocableMethod(label='Get System Health' description='Data freshness (metrics, trends, knowledge), failed jobs, quality-checker availability, posting volume, open owner items and open improvement requests.')
    public static List<Result> run(List<Request> requests) {
        Result r = new Result();
        r.healthJson = JSON.serialize(report(Datetime.now()));
        return new List<Result>{ r };
    }

    public static Map<String, Object> report(Datetime now) {
        Map<String, Object> h = new Map<String, Object>();
        List<Social_Post__c> m = [SELECT Metrics_Updated_At__c FROM Social_Post__c WHERE Metrics_Updated_At__c != null ORDER BY Metrics_Updated_At__c DESC LIMIT 1];
        h.put('metrics_hours_old', m.isEmpty() ? null : hours(m[0].Metrics_Updated_At__c, now));
        List<Trend_Brief__c> t = [SELECT CreatedDate FROM Trend_Brief__c ORDER BY CreatedDate DESC LIMIT 1];
        h.put('trends_hours_old', t.isEmpty() ? null : hours(t[0].CreatedDate, now));
        h.put('verified_facts', [SELECT COUNT() FROM Anime_Knowledge__c]);
        h.put('failed_jobs_24h', [SELECT COUNT() FROM Video_Job__c WHERE Status__c = 'Failed' AND CreatedDate >= :now.addHours(-24)]);
        h.put('posts_24h', [SELECT COUNT() FROM Social_Post__c WHERE Status__c = 'Published' AND Published_At__c >= :now.addHours(-24)]);
        Integer checked = 0, unchecked = 0;
        Integer unplanned = 0;
        for (Anime_Content__c c : [SELECT Render_Report__c, Showrunner_Directive__c, Slot__c, CreatedDate FROM Anime_Content__c
                                   WHERE CreatedDate >= :now.addDays(-7) LIMIT 60]) {
            if (c.Slot__c != null && String.isBlank(c.Showrunner_Directive__c)) unplanned++;
            if (c.CreatedDate < now.addDays(-2) || String.isBlank(c.Render_Report__c)) continue;
            if (c.Render_Report__c.contains('"status":"checked"')) checked++; else unchecked++;
        }
        h.put('quality_checker_last_2_days', new Map<String, Object>{ 'checked' => checked, 'not_checked' => unchecked });
        h.put('reels_without_agent_plan_7d', unplanned);
        h.put('open_owner_items', [SELECT COUNT() FROM Agent_Activity__c WHERE Needs_Owner__c = true AND Resolved__c = false]);
        List<String> requests = new List<String>();
        for (Agent_Activity__c a : [SELECT Details__c FROM Agent_Activity__c WHERE Action__c IN ('Improvement request', 'Feature request')
                                    AND Resolved__c = false ORDER BY CreatedDate DESC LIMIT 8]) {
            requests.add(String.valueOf(a.Details__c).abbreviate(200));
        }
        h.put('open_improvement_requests', requests);
        h.put('expectations', 'metrics under 8 h old before planning; trends under 8 h; failed jobs 0; quality checker should check every Reel');
        return h;
    }

    static Decimal hours(Datetime t, Datetime now) {
        return (Decimal.valueOf(now.getTime() - t.getTime()) / 3600000).setScale(1);
    }
}