/**
 * Weekly Performance Analyst agent: reviews recent Reels + Instagram metrics and writes a
 * Performance_Review__c plus Topic_Idea__c backlog entries. Schedule: PerformanceAnalyst.scheduleWeekly()
 */
public with sharing class PerformanceAnalyst implements Schedulable {
    public static final String JOB_NAME = 'InstaAgent Performance Analyst';
    public static final String TEMPLATE = 'Insta_Performance_Analyst';

    public void execute(SchedulableContext context) {
        System.enqueueJob(new Job()); // LLM work runs async
    }

    /** Async worker (a class can't be both Schedulable and Queueable). */
    public class Job implements Queueable, Database.AllowsCallouts {
        public void execute(QueueableContext context) {
            run(Date.today());
        }
    }

    public static Performance_Review__c run(Date periodEnd) {
        Datetime since = Datetime.newInstance(periodEnd.addDays(-30), Time.newInstance(0, 0, 0, 0));
        List<Map<String, Object>> reels = new List<Map<String, Object>>();
        for (Anime_Content__c c : [
            SELECT Format__c, Anime__r.Name, Slot__c, Hook__c, Quality_Score__c, Script_Source__c, Duration_Seconds__c, Status__c,
                (SELECT Views__c, Reach__c, Avg_Watch_Time_Ms__c, Likes__c, Comments__c, Shares__c, Saves__c FROM Social_Posts__r LIMIT 1)
            FROM Anime_Content__c
            WHERE CreatedDate >= :since AND Status__c IN ('Published', 'Rendered')
            ORDER BY CreatedDate DESC
            LIMIT 40
        ]) {
            Map<String, Object> row = new Map<String, Object>{
                'format' => c.Format__c, 'anime' => c.Anime__r == null ? null : c.Anime__r.Name, 'slot' => c.Slot__c,
                'hook' => c.Hook__c, 'director_score' => c.Quality_Score__c, 'script_source' => c.Script_Source__c,
                'duration' => c.Duration_Seconds__c, 'published' => c.Status__c == 'Published'
            };
            if (!c.Social_Posts__r.isEmpty()) {
                Social_Post__c p = c.Social_Posts__r[0];
                row.putAll(new Map<String, Object>{
                    'views' => p.Views__c, 'reach' => p.Reach__c, 'avg_watch_ms' => p.Avg_Watch_Time_Ms__c, 'likes' => p.Likes__c,
                    'comments' => p.Comments__c, 'shares' => p.Shares__c, 'saves' => p.Saves__c
                });
            }
            reels.add(row);
        }
        List<String> trending = new List<String>();
        for (Anime__c a : [SELECT Name FROM Anime__c WHERE Active__c = true ORDER BY LastModifiedDate DESC LIMIT 10]) {
            trending.add(a.Name);
        }
        Map<String, Object> out = PromptRunner.runJson(TEMPLATE, new Map<String, Object>{ 'reels' => reels, 'trending' => trending });

        Performance_Review__c review = new Performance_Review__c(
            Period_End__c = periodEnd,
            Summary__c = String.valueOf(out.get('summary')).abbreviate(5000),
            Feature_Anime__c = joinList(out.get('feature_anime')).abbreviate(255),
            Avoid_Anime__c = joinList(out.get('avoid_anime')).abbreviate(255),
            Hook_Lessons__c = joinLines(out.get('hook_lessons')).abbreviate(5000),
            Recommendations__c = JSON.serialize(out).abbreviate(32000),
            Reels_Analysed__c = reels.size()
        );
        insert review;
        TopicBacklog.add(out.get('topic_ideas'), 'Analyst');
        return review;
    }

    public static String joinList(Object value) {
        List<String> parts = new List<String>();
        if (value instanceof List<Object>) {
            for (Object o : (List<Object>) value) {
                parts.add(String.valueOf(o));
            }
        }
        return String.join(parts, ', ');
    }

    public static String joinLines(Object value) {
        return joinList(value).replace(', ', '\n');
    }

    public static Id scheduleWeekly() {
        for (CronTrigger ct : [SELECT Id FROM CronTrigger WHERE CronJobDetail.Name = :JOB_NAME]) {
            System.abortJob(ct.Id);
        }
        return System.schedule(JOB_NAME, '0 30 2 ? * MON', new PerformanceAnalyst()); // Mondays 08:00 IST (02:30 GMT; org TZ independent via scheduler user TZ)
    }
}
