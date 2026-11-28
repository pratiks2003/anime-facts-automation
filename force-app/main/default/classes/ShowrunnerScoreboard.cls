/** Showrunner action: the scoreboard - what wins and loses on this account, by show / format / slot / effect / style. */
public with sharing class ShowrunnerScoreboard {
    public class Request {
        @InvocableVariable(label='Show' description='Optional: only this anime')
        public String show;
    }
    public class Result {
        @InvocableVariable(label='Scoreboard' description='Evidence as JSON')
        public String scoreboardJson;
    }

    @InvocableMethod(label='Get Scoreboard' description='Performance index (1.00 = normal for this account) and win/loss evidence by show, format, slot, effect, style and planner; drop-rule status per show; data freshness.')
    public static List<Result> run(List<Request> requests) {
        Result r = new Result();
        r.scoreboardJson = JSON.serialize(Scoreboard.insights(Datetime.now(), requests.isEmpty() ? null : requests[0].show));
        return new List<Result>{ r };
    }
}