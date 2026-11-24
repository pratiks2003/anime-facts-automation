/** Topic_Idea__c backlog helpers shared by the analyst, community manager, public votes and the admin. */
public with sharing class TopicBacklog {
    public static Integer add(Object ideas, String source) {
        if (!(ideas instanceof List<Object>)) {
            return 0;
        }
        Set<String> existing = new Set<String>();
        for (Topic_Idea__c t : [SELECT Name FROM Topic_Idea__c WHERE Status__c = 'New' LIMIT 500]) {
            existing.add(t.Name.toLowerCase());
        }
        Set<String> formats = new Set<String>();
        for (Schema.PicklistEntry e : Topic_Idea__c.Format__c.getDescribe().getPicklistValues()) {
            formats.add(e.getValue());
        }
        List<Topic_Idea__c> rows = new List<Topic_Idea__c>();
        for (Object o : (List<Object>) ideas) {
            if (!(o instanceof Map<String, Object>)) {
                continue;
            }
            Map<String, Object> idea = (Map<String, Object>) o;
            String text = String.valueOf(idea.get('idea'));
            if (String.isBlank(text) || existing.contains(text.abbreviate(80).toLowerCase())) {
                continue;
            }
            String format = String.valueOf(idea.get('format'));
            rows.add(new Topic_Idea__c(
                Name = text.abbreviate(80),
                Anime__c = String.valueOf(idea.get('anime')).abbreviate(120),
                Format__c = formats.contains(format) ? format : null,
                Source__c = source,
                Status__c = 'New',
                Votes__c = 0
            ));
            existing.add(text.abbreviate(80).toLowerCase());
        }
        insert rows;
        return rows.size();
    }
}
