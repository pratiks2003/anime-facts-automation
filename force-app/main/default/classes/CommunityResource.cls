/**
 * POST /services/apexrest/insta/studio/comments
 * {"media_id": "...", "reel": {...}, "comments": [{"id": "...", "text": "...", "likes": n}]}
 * Community Manager agent writes a reply for every NEW comment, logs each one as a Comment_Reply__c
 * (spam / unsafe are logged as Skipped and never answered) and returns what GitHub should post.
 */
@RestResource(urlMapping='/insta/studio/comments')
global with sharing class CommunityResource {
    public static final Set<String> NO_REPLY = new Set<String>{ 'spam', 'unsafe' };

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
            // only comments we have never handled
            Set<String> ids = new Set<String>();
            for (Object o : comments) {
                ids.add(String.valueOf(((Map<String, Object>) o).get('id')));
            }
            Set<String> handled = new Set<String>();
            for (Comment_Reply__c c : [SELECT Comment_Id__c FROM Comment_Reply__c WHERE Comment_Id__c IN :ids]) {
                handled.add(c.Comment_Id__c);
            }
            List<Object> fresh = new List<Object>();
            Map<String, String> textById = new Map<String, String>();
            for (Object o : comments) {
                Map<String, Object> c = (Map<String, Object>) o;
                String id = String.valueOf(c.get('id'));
                if (!handled.contains(id)) {
                    fresh.add(c);
                    textById.put(id, String.valueOf(c.get('text')));
                }
            }
            Boolean autoReply = InstaAgentConfig.get().Auto_Reply_Comments__c == true;
            if (fresh.isEmpty()) {
                res.statusCode = 200;
                res.responseBody = Blob.valueOf(JSON.serialize(new Map<String, Object>{ 'auto_reply' => autoReply, 'replies' => new List<Object>(), 'ideas_added' => 0 }));
                return;
            }
            Map<String, Object> out = PromptRunner.runJson(
                'Insta_Community_Manager',
                new Map<String, Object>{ 'reel' => request.get('reel'), 'comments' => fresh }
            );
            List<Social_Post__c> posts = [SELECT Id FROM Social_Post__c WHERE Instagram_Media_Id__c = :mediaId LIMIT 1];
            Id postId = posts.isEmpty() ? null : posts[0].Id;
            Set<String> categories = new Set<String>();
            for (Schema.PicklistEntry e : Comment_Reply__c.Category__c.getDescribe().getPicklistValues()) {
                categories.add(e.getValue());
            }
            List<Comment_Reply__c> rows = new List<Comment_Reply__c>();
            List<Object> toPost = new List<Object>();
            Set<String> answered = new Set<String>();
            if (out.get('replies') instanceof List<Object>) {
                for (Object o : (List<Object>) out.get('replies')) {
                    Map<String, Object> r = (Map<String, Object>) o;
                    String id = String.valueOf(r.get('comment_id'));
                    if (!textById.containsKey(id) || answered.contains(id)) {
                        continue; // unknown or duplicate id from the model
                    }
                    answered.add(id);
                    String category = categories.contains(String.valueOf(r.get('category'))) ? String.valueOf(r.get('category')) : 'other';
                    String reply = r.get('reply') == null ? '' : String.valueOf(r.get('reply')).normalizeSpace().left(300);
                    Boolean skip = NO_REPLY.contains(category) || String.isBlank(reply);
                    rows.add(new Comment_Reply__c(
                        Comment_Id__c = id, Social_Post__c = postId, Comment_Text__c = textById.get(id).left(2200),
                        Reply_Text__c = reply, Category__c = category, Status__c = skip ? 'Skipped' : 'Drafted'
                    ));
                    if (!skip && autoReply) {
                        toPost.add(new Map<String, Object>{ 'comment_id' => id, 'reply' => reply });
                    }
                }
            }
            upsert rows Comment_Id__c;
            if (postId != null) {
                update new Social_Post__c(Id = postId, Comments_Reviewed_At__c = Datetime.now());
            }
            Integer ideas = TopicBacklog.add(out.get('topic_ideas'), 'Comments');
            res.statusCode = 200;
            res.responseBody = Blob.valueOf(JSON.serialize(new Map<String, Object>{ 'auto_reply' => autoReply, 'replies' => toPost, 'ideas_added' => ideas }));
        } catch (Exception e) {
            res.statusCode = 500;
            res.responseBody = Blob.valueOf(JSON.serialize(new Map<String, Object>{ 'error' => e.getTypeName() + ': ' + e.getMessage() }));
        }
    }
}
