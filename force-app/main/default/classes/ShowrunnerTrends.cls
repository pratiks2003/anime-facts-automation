/** Showrunner action: today's cross-checked trends (hot topics + show ranking) from the latest research brief. */
public with sharing class ShowrunnerTrends {
    public class Request {
        @InvocableVariable(label='Show filter' description='Optional: only this show (leave empty for everything)')
        public String show;
    }

    public class Result {
        @InvocableVariable(label='Trends' description='JSON: summary, hot topics with heat, show ranking with how many sources agree')
        public String trendsJson;
    }

    @InvocableMethod(label='Get Trends' description='Latest research: hot topics (heat 1-10) and shows ranked by how many independent sources agree.')
    public static List<Result> run(List<Request> requests) {
        Result r = new Result();
        List<Trend_Brief__c> briefs = [SELECT Brief_Date__c, Summary__c, Brief_JSON__c, CreatedDate FROM Trend_Brief__c ORDER BY CreatedDate DESC LIMIT 1];
        if (briefs.isEmpty()) {
            r.trendsJson = '{}';
            return new List<Result>{ r };
        }
        Object brief = ShowrunnerSupport.parse(briefs[0].Brief_JSON__c);
        Map<String, Object> out = new Map<String, Object>{ 'researched_at' => briefs[0].CreatedDate, 'summary' => briefs[0].Summary__c };
        if (brief instanceof Map<String, Object>) {
            Map<String, Object> b = (Map<String, Object>) brief;
            out.put('hot_topics', b.get('hot_topics'));
            List<Object> ranking = new List<Object>();
            if (b.get('show_ranking') instanceof List<Object>) {
                for (Object o : (List<Object>) b.get('show_ranking')) {
                    if (ranking.size() >= 10) break;
                    Map<String, Object> s = (Map<String, Object>) o;
                    ranking.add(new Map<String, Object>{ 'show' => s.get('anime'), 'score' => s.get('score'),
                        'sources_agreeing' => s.get('sources_agreeing'), 'main_show' => s.get('main_show'),
                        'verified_facts' => s.get('verified_facts'), 'evidence' => s.get('evidence') });
                }
            }
            out.put('show_ranking', ranking);
        }
        r.trendsJson = JSON.serialize(out).abbreviate(60000);
        return new List<Result>{ r };
    }
}