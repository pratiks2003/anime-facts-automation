/**
 * POST /services/apexrest/insta/studio/comments
 * {"media_id": "...", "reel": {...}, "comments": [{"text": "...", "likes": n}]}
 * Community Manager agent: stores reply drafts on the Social Post and adds topic ideas.
 */
@RestResource(urlMapping='/insta/studio/comments')
global with sharing class CommunityResource {
    @HttpPost
    global static void run() {
        RestResponse res = RestContext.response;
        res.addHeader('Content-Type', 'application/json');
        try {
            Map<String, Object> request = (Map<String, Object>) JSON.deserializeUntyped(RestContext.request.requestBody.toString());
            List<Object> comments = (List<Object>) request.get('comments');
            String mediaId = (String) request.get('media_id');
            if (String.isBlank(mediaId) || comments == null || comments.isEmpty()) {
                res.statusCode = 400;
                res.responseBody = Blob.valueOf('{"error":"media_id and comments are required"}');
                return;
            }
            Map<String, Object> out = PromptRunner.runJson(
                'Insta_Community_Manager',
                new Map<String, Object>{ 'reel' => request.get('reel'), 'comments' => comments }
            );
            List<Social_Post__c> posts = [SELECT Id FROM Social_Post__c WHERE Instagram_Media_Id__c = :mediaId LIMIT 1];
            if (!posts.isEmpty()) {
                update new Social_Post__c(
                    Id = posts[0].Id,
                    Reply_Drafts__c = JSON.serialize(out.get('replies')).abbreviate(32000),
                    Comments_Reviewed_At__c = Datetime.now()
                );
            }
            Integer ideas = TopicBacklog.add(out.get('topic_ideas'), 'Comments');
            res.statusCode = 200;
            res.responseBody = Blob.valueOf(JSON.serialize(new Map<String, Object>{ 'replies' => out.get('replies'), 'ideas_added' => ideas }));
        } catch (Exception e) {
            res.statusCode = 500;
            res.responseBody = Blob.valueOf(JSON.serialize(new Map<String, Object>{ 'error' => e.getTypeName() + ': ' + e.getMessage() }));
        }
    }
}
