/**
 * The agent team that writes a Reel: Script Writer -> Creative Director (score, brief,
 * up to 2 rewrites) -> Fact Checker (+1 Director fix pass) -> Caption Writer.
 * At most 7 Einstein generations per Reel (free tier: 150 outputs/hour).
 */
public with sharing class ScriptStudio {
    public static final String WRITER = 'Insta_Script_Writer';
    public static final String DIRECTOR = 'Insta_Creative_Director';
    public static final String FACT_CHECKER = 'Insta_Fact_Checker';
    public static final String CAPTION = 'Insta_Caption_Writer';
    public static final Integer MAX_REWRITES = 2;

    public class Result {
        public Map<String, Object> script;
        public Decimal overall;
        public Map<String, Object> scores;
        public List<Object> notes = new List<Object>();
        public Map<String, Object> production;
        public Boolean factsPass = true;
        public List<Object> factIssues = new List<Object>();
        public Integer calls = 0;

        public Map<String, Object> toMap() {
            return new Map<String, Object>{
                'script' => script,
                'quality' => new Map<String, Object>{ 'overall' => overall, 'scores' => scores, 'notes' => notes },
                'production' => production,
                'fact_check' => new Map<String, Object>{ 'pass' => factsPass, 'issues' => factIssues },
                'calls' => calls
            };
        }
    }

    /**
     * request: {content_id, format, guide{mode,shape,tone}, anime, context, topic, facts[{id,anime,title,content,characters,spoiler_level}]}
     */
    public static Result run(Map<String, Object> request) {
        Integer startCalls = PromptRunner.calls;
        Result r = new Result();
        Map<String, Object> writerReq = new Map<String, Object>{
            'format' => request.get('format'),
            'anime' => request.get('anime'),
            'context' => request.get('context'),
            'topic' => request.get('topic'),
            'facts' => request.get('facts')
        };
        Map<String, Object> guide = (Map<String, Object>) request.get('guide');
        if (guide != null) {
            writerReq.putAll(guide);
        }
        r.script = PromptRunner.runJson(WRITER, writerReq);

        // Creative Director: score + brief; rewrite while it asks to (bounded).
        Map<String, Object> verdict;
        for (Integer i = 0; i <= MAX_REWRITES; i++) {
            verdict = direct(request, r.script, null);
            Object revised = verdict.get('revised_script');
            if ((String) verdict.get('verdict') != 'revise' || !(revised instanceof Map<String, Object>) || i == MAX_REWRITES) {
                break;
            }
            r.script = (Map<String, Object>) revised;
        }
        applyVerdict(r, verdict);

        // Fact Checker, with one targeted Director fix pass.
        Map<String, Object> check = factCheck(request, r.script);
        if (!(Boolean) check.get('pass')) {
            Map<String, Object> fix = direct(request, r.script, (List<Object>) check.get('issues'));
            if (fix.get('revised_script') instanceof Map<String, Object>) {
                r.script = (Map<String, Object>) fix.get('revised_script');
                applyVerdict(r, fix);
                check = factCheck(request, r.script);
            }
        }
        r.factsPass = (Boolean) check.get('pass');
        r.factIssues = (List<Object>) check.get('issues');

        // Caption & hashtags written for the final script.
        Map<String, Object> caption = PromptRunner.runJson(
            CAPTION,
            new Map<String, Object>{ 'format' => request.get('format'), 'anime' => request.get('anime'), 'script' => r.script }
        );
        if (caption.get('caption') != null) {
            r.script.put('caption', caption.get('caption'));
        }
        if (caption.get('hashtags') instanceof List<Object>) {
            r.script.put('hashtags', caption.get('hashtags'));
        }
        r.calls = PromptRunner.calls - startCalls;
        return r;
    }

    private static Map<String, Object> direct(Map<String, Object> request, Map<String, Object> script, List<Object> issues) {
        Map<String, Object> req = new Map<String, Object>{
            'format' => request.get('format'),
            'facts' => request.get('facts'),
            'script' => script
        };
        if (issues != null && !issues.isEmpty()) {
            req.put('issues', issues);
        }
        return PromptRunner.runJson(DIRECTOR, req);
    }

    private static Map<String, Object> factCheck(Map<String, Object> request, Map<String, Object> script) {
        Map<String, Object> check = PromptRunner.runJson(
            FACT_CHECKER,
            new Map<String, Object>{ 'script' => script.get('scenes'), 'facts' => request.get('facts') }
        );
        if (!(check.get('pass') instanceof Boolean)) {
            check.put('pass', false);
        }
        if (!(check.get('issues') instanceof List<Object>)) {
            check.put('issues', new List<Object>());
        }
        return check;
    }

    private static void applyVerdict(Result r, Map<String, Object> verdict) {
        Object overall = verdict.get('overall');
        r.overall = overall == null ? null : Decimal.valueOf(String.valueOf(overall)).setScale(1);
        r.scores = (Map<String, Object>) verdict.get('scores');
        r.notes = verdict.get('notes') instanceof List<Object> ? (List<Object>) verdict.get('notes') : new List<Object>();
        if (verdict.get('production') instanceof Map<String, Object>) {
            r.production = (Map<String, Object>) verdict.get('production');
        }
    }

    /** Persist the quality picture on the Reel (best effort; the REST caller also gets it). */
    public static void record(String contentId, Result r) {
        if (String.isBlank(contentId)) {
            return;
        }
        List<Anime_Content__c> rows = [SELECT Id FROM Anime_Content__c WHERE External_Id__c = :contentId LIMIT 1];
        if (rows.isEmpty()) {
            return;
        }
        List<String> lines = new List<String>();
        for (Object note : r.notes) {
            lines.add(String.valueOf(note));
        }
        String notes = String.join(lines, '\n');
        if (!r.factsPass) {
            notes += '\nFact check issues: ' + JSON.serialize(r.factIssues);
        }
        update new Anime_Content__c(
            Id = rows[0].Id,
            Quality_Score__c = r.overall,
            Director_Notes__c = notes.abbreviate(5000),
            Production_Brief__c = JSON.serialize(r.production == null ? new Map<String, Object>() : r.production).abbreviate(5000),
            Script_Source__c = 'Agentforce'
        );
    }
}
