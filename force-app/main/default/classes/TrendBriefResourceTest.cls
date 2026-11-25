@IsTest
private class TrendBriefResourceTest {
    @IsTest
    static void signalsBecomeBriefAndIdeas() {
        PromptRunner.mock = new Map<String, List<String>>{
            TrendBriefResource.TEMPLATE => new List<String>{
                '{"summary":"Sakamoto Days is surging.","rising_anime":[{"anime":"Sakamoto Days","why":"3x breakouts"}],' +
                '"winning_topics":[{"anime":"Sakamoto Days","idea":"Why Sakamoto got fat","hook":"He retired for love"}],' +
                '"hook_patterns":[],"style_signals":["2s cuts"],"our_gaps":[],"trending_sound":{"wanted":true,"note":"phonk edit"}}'
            }
        };
        RestContext.request = new RestRequest();
        RestContext.response = new RestResponse();
        RestContext.request.requestBody = Blob.valueOf('{"watchlist":[],"anilist":[{"title":"Sakamoto Days"}],"ours":[]}');
        TrendBriefResource.run();
        Assert.areEqual(200, RestContext.response.statusCode, RestContext.response.responseBody.toString());
        Trend_Brief__c b = [SELECT Summary__c, Needs_Trending_Sound__c, Signals_JSON__c FROM Trend_Brief__c];
        Assert.isTrue(b.Needs_Trending_Sound__c);
        Assert.isTrue(b.Signals_JSON__c.contains('Sakamoto'));
        Assert.areEqual('Trends', [SELECT Source__c FROM Topic_Idea__c].Source__c);

        RestContext.request.requestBody = Blob.valueOf('{}'); // no mock left -> 500
        TrendBriefResource.run();
        Assert.areEqual(500, RestContext.response.statusCode);
    }

    @IsTest
    static void watchlistIsCleanedAndServed() {
        InstaAgentTestUtil.settings(true, false);
        Assert.areEqual('crunchyroll,anime.page,x_y', InstaAgentConfig.cleanWatchlist('@Crunchyroll, anime.page\nx_y bad!name crunchyroll'));
        Map<String, Object> values = new Map<String, Object>{ 'automationEnabled' => false, 'twoReelsPerDay' => false, 'autoApprove' => true,
            'dryRun' => true, 'watchlist' => '@crunchyroll, toonami' };
        AdminPanelController.saveSettings(values);
        RestContext.request = new RestRequest();
        RestContext.response = new RestResponse();
        TrendBriefResource.config();
        Assert.isTrue(RestContext.response.responseBody.toString().contains('toonami'));
    }
}
