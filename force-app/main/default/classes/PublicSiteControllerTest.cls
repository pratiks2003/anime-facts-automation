@IsTest
private class PublicSiteControllerTest {
    class Mock implements HttpCalloutMock {
        public HttpResponse respond(HttpRequest req) {
            HttpResponse res = new HttpResponse();
            res.setStatusCode(200);
            String url = req.getEndpoint();
            if (url.contains('anilist')) {
                Long t = Datetime.now().getTime() / 1000 + 3600;
                res.setBody('{"data":{"Page":{"airingSchedules":[' +
                    '{"episode":10,"airingAt":' + t + ',"media":{"id":1,"isAdult":false,"popularity":900,"averageScore":86,"siteUrl":"u","title":{"english":"JoJo"},"coverImage":{"large":"https://s4.anilist.co/a.jpg"}}},' +
                    '{"episode":11,"airingAt":' + (t + 600000) + ',"media":{"id":1,"isAdult":false,"popularity":900,"title":{"english":"JoJo"},"coverImage":{"large":"x"}}},' +
                    '{"episode":3,"airingAt":' + t + ',"media":{"id":2,"isAdult":true,"popularity":5,"title":{"english":"Nope"},"coverImage":{"large":"x"}}}]}}}');
            } else if (url.endsWith('anime_metadata.json')) {
                res.setBody('{"anime":[{"name":"One Piece","knowledge_file":"one_piece.jsonl"},{"name":"Empty","knowledge_file":"missing.jsonl"}]}');
            } else if (url.endsWith('one_piece.jsonl')) {
                res.setBody('{"id":"OP-1","anime":"One Piece","title":"Rubber","content":"Luffy is rubber.","verified":true,"spoiler_level":"none","canon_status":"canon","source_url":"s","category":"ability"}\n' +
                    '{"id":"OP-2","anime":"One Piece","title":"Secret","content":"Big spoiler.","verified":true,"spoiler_level":"high","canon_status":"canon","source_url":"s"}\n' +
                    '{"id":"OP-3","anime":"One Piece","title":"Unverified","content":"Maybe.","verified":false,"spoiler_level":"none","canon_status":"canon","source_url":"s"}\n');
            } else {
                res.setStatusCode(404);
            }
            return res;
        }
    }

    @IsTest
    static void weeklyDropsDedupesAndHidesAdult() {
        Test.setMock(HttpCalloutMock.class, new Mock());
        List<Map<String, Object>> drops = PublicSiteController.weeklyDrops();
        Assert.areEqual(1, drops.size());
        Assert.areEqual(10, drops[0].get('episode'));
        Assert.isTrue(((String) drops[0].get('airs')).endsWith('IST'));
    }

    @IsTest
    static void factsOnlyVerifiedLowSpoiler() {
        Test.setMock(HttpCalloutMock.class, new Mock());
        List<Map<String, Object>> facts = PublicSiteController.facts();
        Assert.areEqual(1, facts.size());
        Assert.areEqual('OP-1', facts[0].get('id'));
    }

    @IsTest
    static void reelsIdeasVotingAndSuggestions() {
        Anime_Content__c c = new Anime_Content__c(Name = 'Drops', External_Id__c = 'R-P', Format__c = 'Quiz', Status__c = 'Published');
        insert c;
        insert new Social_Post__c(Content__c = c.Id, Instagram_Media_Id__c = 'M', Permalink__c = 'https://www.instagram.com/reel/x/', Status__c = 'Published', Published_At__c = Datetime.now());
        Topic_Idea__c open = new Topic_Idea__c(Name = 'Guess the Stand', Status__c = 'New', Votes__c = 2);
        Topic_Idea__c used = new Topic_Idea__c(Name = 'Old', Status__c = 'Used');
        insert new List<Topic_Idea__c>{ open, used };

        Assert.areEqual(1, PublicSiteController.latestReels(6).size());
        Assert.areEqual(1, PublicSiteController.ideas().size());
        Assert.areEqual(3, PublicSiteController.vote(open.Id));
        for (String bad : new List<String>{ used.Id, 'not-an-id', c.Id }) {
            try {
                PublicSiteController.vote(bad);
                Assert.fail('vote should be rejected for ' + bad);
            } catch (AuraHandledException e) {
                Assert.isNotNull(e.getMessage());
            }
        }
        PublicSiteController.suggest('One Piece', '<b>Zoro</b> gets lost in every arc');
        Topic_Idea__c s = [SELECT Name, Status__c, Source__c FROM Topic_Idea__c WHERE Source__c = 'Public Vote'];
        Assert.areEqual('Suggested', s.Status__c);
        Assert.areEqual('Zoro gets lost in every arc', s.Name);
        try {
            PublicSiteController.suggest('', 'short');
            Assert.fail('too short');
        } catch (AuraHandledException e) {
            Assert.isTrue(e.getMessage().contains('8+'));
        }
    }
}
