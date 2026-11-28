/**
 * Showrunner action: keyword search over every verified fact (Anime_Knowledge__c, refreshed by GitHub after each
 * render). Complements search_knowledge (Data Cloud, by meaning), which is refreshed less often.
 */
public with sharing class ShowrunnerFindFacts {
    public class Request {
        @InvocableVariable(label='Search' description='Names or keywords, e.g. "Ivy spiriflor" or "Gear 5"' required=true)
        public String searchText;
        @InvocableVariable(label='Show' description='Optional: only facts about this anime')
        public String show;
    }
    public class Result {
        @InvocableVariable(label='Facts' description='Matching facts as JSON: id, show, title, fact, canon status, spoiler level, source')
        public String factsJson;
    }

    @InvocableMethod(label='Find Facts' description='Keyword search over all verified anime facts, including the newest ones.')
    public static List<Result> run(List<Request> requests) {
        Request q = requests[0];
        Result r = new Result();
        String text = q.searchText == null ? '' : q.searchText.replaceAll('[?&|!{}\\[\\]()^~*:\\\\"\'+-]', ' ').normalizeSpace();
        List<Map<String, Object>> rows = new List<Map<String, Object>>();
        if (text.length() >= 2) {
            String terms = String.join(text.split(' '), ' OR ');
            List<List<SObject>> found = [FIND :terms IN ALL FIELDS RETURNING Anime_Knowledge__c(Knowledge_Id__c, Name, Content__c,
                Anime__r.Name, Canon_Status__c, Spoiler_Level__c, Source_URL__c, Verified__c) LIMIT 40];
            for (Anime_Knowledge__c k : (List<Anime_Knowledge__c>) found[0]) {
                String show = k.Anime__r == null ? null : k.Anime__r.Name;
                if (String.isNotBlank(q.show) && show != null && !show.containsIgnoreCase(q.show.trim())
                        && !q.show.trim().containsIgnoreCase(show)) {
                    continue;
                }
                rows.add(new Map<String, Object>{
                    'id' => k.Knowledge_Id__c, 'show' => show, 'title' => k.Name, 'fact' => k.Content__c,
                    'canon_status' => k.Canon_Status__c, 'spoiler_level' => k.Spoiler_Level__c,
                    'verified' => k.Verified__c, 'source' => k.Source_URL__c
                });
                if (rows.size() == 10) break;
            }
        }
        r.factsJson = JSON.serialize(rows);
        return new List<Result>{ r };
    }
}