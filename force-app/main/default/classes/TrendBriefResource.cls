/**
 * POST /services/apexrest/insta/research/brief  (daily research.yml): signals -> Trend Analyst -> Trend_Brief__c
 */
@RestResource(urlMapping='/insta/research/brief')
global with sharing class TrendBriefResource {
    public static final String TEMPLATE = 'Insta_Trend_Analyst';

    /** GET -> research config (the watchlist lives in the protected custom setting). */
    @HttpGet
    global static void config() {
        RestResponse res = RestContext.response;
        res.addHeader('Content-Type', 'application/json');
        res.statusCode = 200;
        res.responseBody = Blob.valueOf(JSON.serialize(new Map<String, Object>{ 'watchlist' => InstaAgentConfig.watchlist() }));
    }

    @HttpPost
    global static void run() {
        RestResponse res = RestContext.response;
        res.addHeader('Content-Type', 'application/json');
        try {
            Map<String, Object> signals = (Map<String, Object>) JSON.deserializeUntyped(RestContext.request.requestBody.toString());
            res.statusCode = 200;
            res.responseBody = Blob.valueOf(JSON.serialize(build(signals)));
        } catch (Exception e) {
            res.statusCode = 500;
            res.responseBody = Blob.valueOf(JSON.serialize(new Map<String, Object>{ 'error' => e.getTypeName() + ': ' + e.getMessage() }));
        }
    }

    public static Map<String, Object> build(Map<String, Object> signals) {
        List<String> references = new List<String>();
        for (Reference_Reel__c r : [SELECT Lessons__c FROM Reference_Reel__c WHERE Status__c = 'Done' ORDER BY Analyzed_At__c DESC LIMIT 3]) {
            if (r.Lessons__c != null) {
                references.add(r.Lessons__c);
            }
        }
        Map<String, Object> request = new Map<String, Object>{
            'watchlist' => signals.get('watchlist'), 'anilist' => signals.get('anilist'), 'youtube' => signals.get('youtube'),
            'ours' => signals.get('ours'), 'references' => references
        };
        Map<String, Object> out = PromptRunner.runJson(TEMPLATE, request);
        Boolean soundWanted = false;
        if (out.get('trending_sound') instanceof Map<String, Object>) {
            soundWanted = ((Map<String, Object>) out.get('trending_sound')).get('wanted') == true;
        }
        Trend_Brief__c brief = new Trend_Brief__c(
            Name = ('Brief ' + Date.today().format()).abbreviate(80),
            Brief_Date__c = Date.today(),
            Summary__c = String.valueOf(out.get('summary') == null ? '' : out.get('summary')).abbreviate(5000),
            Brief_JSON__c = JSON.serialize(out).abbreviate(131000),
            Signals_JSON__c = JSON.serialize(signals).abbreviate(131000),
            Needs_Trending_Sound__c = soundWanted
        );
        insert brief;
        Integer ideas = TopicBacklog.add(out.get('winning_topics'), 'Trends');
        return new Map<String, Object>{ 'id' => brief.Id, 'summary' => brief.Summary__c, 'ideas_added' => ideas };
    }
}
