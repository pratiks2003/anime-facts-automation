/**
 * "Learn from a Reel": the admin pastes a public Reel link -> GitHub downloads it (no login), measures it,
 * deletes it and calls back -> the Style Analyst agent turns the fingerprint into lessons + a Testing style.
 */
public with sharing class ReferenceReels {
    public static final String WORKFLOW = 'analyze.yml';
    public static final String TEMPLATE = 'Insta_Style_Analyst';
    private static final Pattern URL_OK = Pattern.compile(
        '^https://(www\\.)?(instagram\\.com/(reel|reels|p)/[\\w-]+|youtube\\.com/shorts/[\\w-]+|youtu\\.be/[\\w-]+|tiktok\\.com/@[\\w.]+/video/\\d+).*'
    );

    public static Boolean validUrl(String url) {
        return url != null && URL_OK.matcher(url.trim()).matches();
    }

    public static void afterInsert(List<Reference_Reel__c> rows) {
        List<Id> queued = new List<Id>();
        for (Reference_Reel__c r : rows) {
            if (r.Status__c == 'Queued' && validUrl(r.URL__c)) {
                queued.add(r.Id);
            }
        }
        if (!queued.isEmpty() && !System.isBatch() && !System.isFuture() && Limits.getQueueableJobs() < Limits.getLimitQueueableJobs()) {
            System.enqueueJob(new Dispatch(queued));
        }
    }

    public class Dispatch implements Queueable, Database.AllowsCallouts {
        private final List<Id> ids;

        public Dispatch(List<Id> ids) {
            this.ids = ids;
        }

        public void execute(QueueableContext context) {
            List<Reference_Reel__c> updates = new List<Reference_Reel__c>();
            for (Reference_Reel__c r : [SELECT Id, URL__c FROM Reference_Reel__c WHERE Id IN :ids]) {
                try {
                    GitHubService.dispatchWorkflow(InstaAgentConfig.repository(), WORKFLOW,
                        new Map<String, String>{ 'reference_id' => r.Id, 'url' => r.URL__c.trim() });
                    updates.add(new Reference_Reel__c(Id = r.Id, Status__c = 'Analyzing', Error__c = null));
                } catch (Exception e) {
                    updates.add(new Reference_Reel__c(Id = r.Id, Status__c = 'Failed', Error__c = e.getMessage().abbreviate(255)));
                }
            }
            update updates;
        }
    }

    /** Callback from GitHub: {id, status: Measured|Failed, analysis, style_limits, error}. */
    public static Map<String, Object> complete(Map<String, Object> body) {
        Id refId = (Id) String.valueOf(body.get('id'));
        Reference_Reel__c ref = [SELECT Id, Name, URL__c, Notes__c FROM Reference_Reel__c WHERE Id = :refId];
        if (body.get('status') != 'Measured') {
            update new Reference_Reel__c(Id = ref.Id, Status__c = 'Failed',
                Error__c = String.valueOf(body.get('error') == null ? 'analysis failed' : body.get('error')).abbreviate(255));
            return new Map<String, Object>{ 'status' => 'Failed' };
        }
        Map<String, Object> analysis = (Map<String, Object>) body.get('analysis');
        Map<String, Object> meta = analysis.get('meta') instanceof Map<String, Object> ? (Map<String, Object>) analysis.get('meta') : new Map<String, Object>();
        Map<String, Object> measured = analysis.clone();
        measured.remove('transcript');
        measured.remove('meta');
        String transcript = String.valueOf(analysis.get('transcript') == null ? '' : analysis.get('transcript'));
        Map<String, Object> out = PromptRunner.runJson(TEMPLATE, new Map<String, Object>{
            'owner_notes' => ref.Notes__c,
            'caption' => meta.get('description') == null ? meta.get('title') : meta.get('description'),
            'author' => meta.get('uploader'),
            'likes' => meta.get('like_count'),
            'views' => meta.get('view_count'),
            'measured' => measured,
            'transcript' => transcript.abbreviate(1500),
            'hook' => analysis.get('hook'),
            'our_style_limits' => body.get('style_limits'),
            'our_current_style' => currentStyle()
        });

        List<String> lessons = new List<String>();
        if (out.get('lessons') instanceof List<Object>) {
            for (Object l : (List<Object>) out.get('lessons')) {
                lessons.add('- ' + String.valueOf(l));
            }
        }
        if (out.get('hook_formula') != null) {
            lessons.add('- Hook formula: ' + out.get('hook_formula'));
        }
        Style__c style;
        if (out.get('style') instanceof Map<String, Object>) {
            Map<String, Object> st = (Map<String, Object>) out.get('style');
            if (st.get('spec') instanceof Map<String, Object>) {
                String name = String.valueOf(st.get('name') == null ? 'Reference ' + Date.today().format() : st.get('name')).abbreviate(70);
                if (![SELECT Id FROM Style__c WHERE Name = :name LIMIT 1].isEmpty()) {
                    name = (name + ' ' + Datetime.now().format('MMdd-HHmm')).abbreviate(80);
                }
                Map<String, Object> spec = (Map<String, Object>) st.get('spec');
                spec.put('name', name);
                style = new Style__c(Name = name, Status__c = 'Testing', Origin__c = 'Reference', Spec__c = JSON.serialize(spec),
                    Description__c = (String.valueOf(st.get('why') == null ? '' : st.get('why')) + ' (learned from ' + ref.URL__c + ')').abbreviate(2000));
                insert style;
            }
        }
        watchAuthor(meta);
        Map<String, Object> stored = analysis.clone();
        stored.put('agent', out);
        update new Reference_Reel__c(
            Id = ref.Id,
            Name = String.valueOf(out.get('summary') == null ? ref.Name : out.get('summary')).abbreviate(80),
            Status__c = 'Done',
            Error__c = null,
            Analyzed_At__c = Datetime.now(),
            Analysis_JSON__c = JSON.serialize(stored).abbreviate(131000),
            Lessons__c = String.join(lessons, '\n').abbreviate(5000),
            Style__c = style == null ? null : style.Id
        );
        return new Map<String, Object>{ 'status' => 'Done', 'summary' => out.get('summary'), 'style' => style == null ? null : style.Name };
    }

    /** Pages the owner learns from are worth watching daily: add the author to the trend watchlist. */
    @TestVisible
    private static void watchAuthor(Map<String, Object> meta) {
        String author = String.valueOf(meta.get('channel') != null ? meta.get('channel') : meta.get('uploader_id'));
        if (author == null || author == 'null') {
            return;
        }
        InstaAgent_Settings__c s = InstaAgentConfig.get();
        String merged = InstaAgentConfig.cleanWatchlist((s.Watchlist__c == null ? '' : s.Watchlist__c + ',') + author);
        if (merged != s.Watchlist__c && s.Id != null) {
            update new InstaAgent_Settings__c(Id = s.Id, Watchlist__c = merged);
        }
    }

    private static Object currentStyle() {
        for (Style__c s : [SELECT Spec__c FROM Style__c WHERE Status__c = 'Active' ORDER BY Last_Used__c DESC NULLS LAST LIMIT 1]) {
            try {
                return JSON.deserializeUntyped(s.Spec__c);
            } catch (Exception e) {
                return null;
            }
        }
        return null;
    }
}
