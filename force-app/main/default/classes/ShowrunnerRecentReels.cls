/**
 * Showrunner action: the last N Reels (show, format, topic, status, quality, metrics) so the agent can judge
 * variety and performance before deciding anything.
 */
public with sharing class ShowrunnerRecentReels {
    public class Request {
        @InvocableVariable(label='How many' description='Number of recent Reels to return (default 6)')
        public Integer count;
    }

    public class Result {
        @InvocableVariable(label='Recent Reels' description='JSON list: id, when, slot, show, format, topic, status, director score, hold reason, views, avg watch seconds')
        public String reelsJson;
    }

    @InvocableMethod(label='Get Recent Reels' description='Returns the most recent Reels with show, format, status, quality scores and Instagram metrics.')
    public static List<Result> run(List<Request> requests) {
        Integer n = requests.isEmpty() || requests[0].count == null ? 6 : Math.min(20, requests[0].count);
        List<Map<String, Object>> rows = new List<Map<String, Object>>();
        for (Anime_Content__c c : [
            SELECT External_Id__c, CreatedDate, Slot__c, Anime__r.Name, Format__c, Topic__c, Status__c, Quality_Score__c,
                Hold_Reason__c, (SELECT Views__c, Avg_Watch_Time_Ms__c FROM Social_Posts__r LIMIT 1)
            FROM Anime_Content__c ORDER BY CreatedDate DESC LIMIT :n
        ]) {
            Social_Post__c p = c.Social_Posts__r.isEmpty() ? null : c.Social_Posts__r[0];
            rows.add(new Map<String, Object>{
                'id' => c.External_Id__c, 'when' => c.CreatedDate, 'slot' => c.Slot__c,
                'show' => c.Anime__r == null ? null : c.Anime__r.Name, 'format' => c.Format__c, 'topic' => c.Topic__c,
                'status' => c.Status__c, 'director_score' => c.Quality_Score__c, 'hold_reason' => c.Hold_Reason__c,
                'views' => p == null ? null : p.Views__c,
                'avg_watch_seconds' => p == null || p.Avg_Watch_Time_Ms__c == null ? null : p.Avg_Watch_Time_Ms__c / 1000
            });
        }
        Result r = new Result();
        r.reelsJson = JSON.serialize(rows);
        return new List<Result>{ r };
    }
}