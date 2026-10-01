#!/usr/bin/env python3
from pathlib import Path
import shutil, subprocess
root = Path("/var/www/lostpet/LostPet_ASPNET/src/LostPet.Web")
if not (root / "Program.cs").exists():
    raise SystemExit("PROJECT NOT FOUND")

def backup(p: Path):
    bak = Path(str(p) + ".bak-analytics")
    if p.exists() and not bak.exists():
        shutil.copy(p, bak)

def write(rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    backup(p)
    p.write_text(text, encoding="utf-8")
    print("wrote", rel)

write("AnalyticsTracker.cs", r'''using System.Data;
using System.Data.Common;
using System.Globalization;
using LostPet.Data;
using Microsoft.EntityFrameworkCore;

namespace LostPet;

public class AnalyticsTracker
{
    private readonly RequestDelegate _next;
    private static readonly SemaphoreSlim Gate = new(1, 1);
    private static volatile bool Ready;

    public AnalyticsTracker(RequestDelegate next) { _next = next; }

    public async Task InvokeAsync(HttpContext ctx)
    {
        try { await TrackAsync(ctx); }
        catch { }
        await _next(ctx);
    }

    public static async Task EnsureAsync(AppDbContext db)
    {
        if (Ready) return;
        await Gate.WaitAsync();
        try
        {
            if (Ready) return;
            await db.Database.ExecuteSqlRawAsync(
                "CREATE TABLE IF NOT EXISTS SiteVisits (" +
                "Id INTEGER PRIMARY KEY AUTOINCREMENT," +
                "SessionKey TEXT NOT NULL," +
                "Path TEXT NOT NULL," +
                "Referrer TEXT," +
                "SourceType TEXT NOT NULL," +
                "SourceName TEXT," +
                "SearchTerms TEXT," +
                "StartedAt TEXT NOT NULL," +
                "DurationSeconds INTEGER," +
                "IsExit INTEGER NOT NULL DEFAULT 1," +
                "Host TEXT)");
            await db.Database.ExecuteSqlRawAsync("CREATE INDEX IF NOT EXISTS IX_SiteVisits_Started ON SiteVisits (StartedAt)");
            await db.Database.ExecuteSqlRawAsync("CREATE INDEX IF NOT EXISTS IX_SiteVisits_Session ON SiteVisits (SessionKey, Id)");
            await db.Database.ExecuteSqlRawAsync(
                "CREATE TABLE IF NOT EXISTS SiteClicks (" +
                "Id INTEGER PRIMARY KEY AUTOINCREMENT," +
                "SessionKey TEXT NOT NULL," +
                "FromPath TEXT," +
                "Href TEXT NOT NULL," +
                "ClickedAt TEXT NOT NULL," +
                "Host TEXT)");
            Ready = true;
        }
        finally { Gate.Release(); }
    }

    static async Task TrackAsync(HttpContext ctx)
    {
        if (!HttpMethods.IsGet(ctx.Request.Method)) return;
        var path = ctx.Request.Path.Value ?? "/";
        if (!ShouldTrack(ctx, path)) return;
        var db = ctx.RequestServices.GetRequiredService<AppDbContext>();
        await EnsureAsync(db);
        var host = (ctx.Request.Host.Host ?? "").ToLowerInvariant();
        var now = DateTime.UtcNow;
        var started = now.ToString("yyyy-MM-ddTHH:mm:ssZ", CultureInfo.InvariantCulture);
        var sid = ctx.Request.Cookies["lp_sid"];
        if (string.IsNullOrEmpty(sid) || sid.Length > 80) sid = Guid.NewGuid().ToString("N");
        var referrer = ctx.Request.Headers.Referer.ToString();
        Classify(referrer, host, out var type, out var name, out var terms);
        var safePath = SafePath(ctx);
        var conn = db.Database.GetDbConnection();
        var opened = conn.State != ConnectionState.Open;
        if (opened) await conn.OpenAsync();
        try
        {
            long? prevId = null;
            DateTime prevAt = default;
            await using (var sel = conn.CreateCommand())
            {
                sel.CommandText = "SELECT Id, StartedAt, DurationSeconds FROM SiteVisits WHERE SessionKey = @sid ORDER BY Id DESC LIMIT 1";
                Add(sel, "@sid", sid);
                await using var reader = await sel.ExecuteReaderAsync();
                if (await reader.ReadAsync())
                {
                    prevId = Convert.ToInt64(reader.GetValue(0));
                    prevAt = DateTime.Parse(reader.GetString(1), CultureInfo.InvariantCulture, DateTimeStyles.AdjustToUniversal | DateTimeStyles.AssumeUniversal);
                }
            }
            if (prevId != null)
            {
                var gap = (int)Math.Clamp((now - prevAt).TotalSeconds, 0, 1800);
                if (gap <= 1800)
                {
                    await using var upd = conn.CreateCommand();
                    upd.CommandText = "UPDATE SiteVisits SET IsExit = 0, DurationSeconds = COALESCE(DurationSeconds, @gap) WHERE Id = @id";
                    Add(upd, "@gap", gap);
                    Add(upd, "@id", prevId.Value);
                    await upd.ExecuteNonQueryAsync();
                }
            }
            long id;
            await using (var ins = conn.CreateCommand())
            {
                ins.CommandText = "INSERT INTO SiteVisits (SessionKey, Path, Referrer, SourceType, SourceName, SearchTerms, StartedAt, IsExit, Host) VALUES (@sid, @path, @ref, @type, @name, @terms, @at, 1, @host)";
                Add(ins, "@sid", sid);
                Add(ins, "@path", safePath);
                Add(ins, "@ref", Cut(referrer, 500));
                Add(ins, "@type", type);
                Add(ins, "@name", name);
                Add(ins, "@terms", (object?)terms ?? DBNull.Value);
                Add(ins, "@at", started);
                Add(ins, "@host", host);
                await ins.ExecuteNonQueryAsync();
            }
            await using (var idCmd = conn.CreateCommand())
            {
                idCmd.CommandText = "SELECT last_insert_rowid()";
                id = Convert.ToInt64(await idCmd.ExecuteScalarAsync());
            }
            var opt = new CookieOptions
            {
                Path = "/",
                Secure = true,
                SameSite = SameSiteMode.Lax,
                IsEssential = true,
                MaxAge = TimeSpan.FromMinutes(30)
            };
            ctx.Response.Cookies.Append("lp_sid", sid, new CookieOptions
            {
                Path = "/",
                Secure = true,
                HttpOnly = true,
                SameSite = SameSiteMode.Lax,
                IsEssential = true,
                MaxAge = TimeSpan.FromMinutes(30)
            });
            opt.HttpOnly = false;
            ctx.Response.Cookies.Append("lp_hit", id.ToString(CultureInfo.InvariantCulture), opt);
        }
        finally
        {
            if (opened) await conn.CloseAsync();
        }
    }

    public static async Task BeatAsync(HttpContext ctx, AppDbContext db)
    {
        try
        {
            if (!ctx.Request.HasFormContentType) return;
            await EnsureAsync(db);
            var form = await ctx.Request.ReadFormAsync();
            if (!long.TryParse(form["id"], out var id)) return;
            if (!int.TryParse(form["seconds"], out var sec)) return;
            sec = Math.Clamp(sec, 0, 1800);
            var sid = ctx.Request.Cookies["lp_sid"];
            if (string.IsNullOrEmpty(sid)) return;
            var conn = db.Database.GetDbConnection();
            var opened = conn.State != ConnectionState.Open;
            if (opened) await conn.OpenAsync();
            try
            {
                await using var cmd = conn.CreateCommand();
                cmd.CommandText = "UPDATE SiteVisits SET DurationSeconds = @sec WHERE Id = @id AND SessionKey = @sid";
                Add(cmd, "@sec", sec);
                Add(cmd, "@id", id);
                Add(cmd, "@sid", sid);
                await cmd.ExecuteNonQueryAsync();
            }
            finally { if (opened) await conn.CloseAsync(); }
        }
        catch { }
    }

    public static async Task ClickAsync(HttpContext ctx, AppDbContext db)
    {
        try
        {
            if (!ctx.Request.HasFormContentType) return;
            await EnsureAsync(db);
            var sid = ctx.Request.Cookies["lp_sid"];
            if (string.IsNullOrEmpty(sid)) return;
            var form = await ctx.Request.ReadFormAsync();
            var href = Cut(form["href"], 500);
            if (!OkHref(href)) return;
            var from = Cut(form["from"], 300);
            var host = (ctx.Request.Host.Host ?? "").ToLowerInvariant();
            var at = DateTime.UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ", CultureInfo.InvariantCulture);
            var conn = db.Database.GetDbConnection();
            var opened = conn.State != ConnectionState.Open;
            if (opened) await conn.OpenAsync();
            try
            {
                await using var cmd = conn.CreateCommand();
                cmd.CommandText = "INSERT INTO SiteClicks (SessionKey, FromPath, Href, ClickedAt, Host) VALUES (@sid, @from, @href, @at, @host)";
                Add(cmd, "@sid", sid);
                Add(cmd, "@from", from);
                Add(cmd, "@href", href);
                Add(cmd, "@at", at);
                Add(cmd, "@host", host);
                await cmd.ExecuteNonQueryAsync();
            }
            finally { if (opened) await conn.CloseAsync(); }
        }
        catch { }
    }

    static bool ShouldTrack(HttpContext ctx, string path)
    {
        var low = path.ToLowerInvariant();
        if (low.StartsWith("/admin") || low.StartsWith("/analytics") || low.StartsWith("/uploads")) return false;
        if (low.Contains('.') || low.Contains("/.")) return false;
        var dest = ctx.Request.Headers["Sec-Fetch-Dest"].ToString();
        if (!string.IsNullOrEmpty(dest) && dest != "document") return false;
        var purpose = (ctx.Request.Headers["Purpose"].ToString() + " " + ctx.Request.Headers["Sec-Purpose"].ToString()).ToLowerInvariant();
        if (purpose.Contains("prefetch") || purpose.Contains("preview")) return false;
        var ua = ctx.Request.Headers.UserAgent.ToString().ToLowerInvariant();
        string[] bots = { "bot", "spider", "crawl", "slurp", "headless", "wget", "curl", "python-requests", "lighthouse", "pagespeed", "semrush", "ahrefs", "petalbot", "bytespider", "gptbot" };
        foreach (var b in bots)
            if (ua.Contains(b)) return false;
        return true;
    }

    static void Classify(string? referrer, string host, out string type, out string name, out string? terms)
    {
        type = "Direct";
        name = "Typed address or bookmark";
        terms = null;
        if (string.IsNullOrWhiteSpace(referrer)) return;
        if (!Uri.TryCreate(referrer, UriKind.Absolute, out var uri)) return;
        var rh = uri.Host.ToLowerInvariant();
        if (SameFamily(rh, host))
        {
            type = "Internal";
            name = "Link on this site";
            return;
        }
        var engine = Engine(rh);
        if (engine != null)
        {
            type = "Search";
            name = engine;
            terms = Terms(uri);
            return;
        }
        type = "Referral";
        name = rh.StartsWith("www.") ? rh.Substring(4) : rh;
    }

    static bool SameFamily(string a, string b)
    {
        static string Root(string h)
        {
            if (h.StartsWith("www.")) h = h.Substring(4);
            if (h.Contains("pettrader") || h.Contains("ebaypet")) return "trader";
            if (h.Contains("lostpet")) return "lostpet";
            return h;
        }
        return Root(a) == Root(b);
    }

    static string? Engine(string host)
    {
        if (host.Contains("google.")) return "Google";
        if (host.Contains("bing.")) return "Bing";
        if (host.Contains("duckduckgo.")) return "DuckDuckGo";
        if (host.Contains("yahoo.")) return "Yahoo";
        if (host.Contains("baidu.")) return "Baidu";
        if (host.Contains("yandex.")) return "Yandex";
        if (host.Contains("ecosia.")) return "Ecosia";
        if (host == "search.brave.com") return "Brave";
        if (host.Contains(".aol.")) return "AOL";
        if (host.Contains("ask.com")) return "Ask";
        return null;
    }

    static string? Terms(Uri uri)
    {
        var q = uri.Query.TrimStart('?');
        if (q.Length == 0) return null;
        foreach (var part in q.Split('&'))
        {
            var i = part.IndexOf('=');
            if (i <= 0) continue;
            var key = Uri.UnescapeDataString(part.Substring(0, i)).ToLowerInvariant();
            if (key == "q" || key == "query" || key == "p" || key == "text" || key == "wd")
            {
                var val = Uri.UnescapeDataString(part.Substring(i + 1).Replace("+", " "));
                if (!string.IsNullOrWhiteSpace(val)) return Cut(val, 120);
            }
        }
        return null;
    }

    static string SafePath(HttpRequest req)
    {
        var path = req.Path.Value ?? "/";
        if (path.Length > 300) path = path.Substring(0, 300);
        var q = req.QueryString.Value ?? "";
        if (q.Length == 0 || q.Length > 120) return path;
        var low = q.ToLowerInvariant();
        if (low.Contains("password") || low.Contains("token") || low.Contains("email") || low.Contains("code=")) return path;
        return path + q;
    }

    static bool OkHref(string href)
    {
        if (string.IsNullOrWhiteSpace(href)) return false;
        var low = href.ToLowerInvariant();
        if (low.StartsWith("javascript:") || low.StartsWith("data:")) return false;
        return low.StartsWith("http://") || low.StartsWith("https://") || low.StartsWith("/");
    }

    static string Cut(string? s, int n)
    {
        if (string.IsNullOrEmpty(s)) return "";
        s = s.Replace("\0", "").Trim();
        return s.Length <= n ? s : s.Substring(0, n);
    }

    static void Add(DbCommand cmd, string name, object value)
    {
        var p = cmd.CreateParameter();
        p.ParameterName = name;
        p.Value = value ?? DBNull.Value;
        cmd.Parameters.Add(p);
    }
}
''')

write("wwwroot/js/analytics.js", r'''(function () {
  var path = (location.pathname || "/").toLowerCase();
  if (path.indexOf("/admin") === 0) return;
  function read(name) {
    var parts = ("; " + document.cookie).split("; " + name + "=");
    if (parts.length < 2) return "";
    return decodeURIComponent(parts.pop().split(";").shift() || "");
  }
  var id = read("lp_hit");
  var start = Date.now();
  function send() {
    if (!id || !navigator.sendBeacon) return;
    var sec = Math.round((Date.now() - start) / 1000);
    if (sec < 0) sec = 0;
    if (sec > 1800) sec = 1800;
    var body = new URLSearchParams();
    body.set("id", id);
    body.set("seconds", String(sec));
    navigator.sendBeacon("/analytics/beat", body);
  }
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "hidden") send();
  });
  window.addEventListener("pagehide", send);
  document.addEventListener("click", function (e) {
    var el = e.target;
    var a = el && el.closest ? el.closest("a[href]") : null;
    if (!a || !navigator.sendBeacon) return;
    var href = a.getAttribute("href") || "";
    if (!href || href.charAt(0) === "#" || href.indexOf("javascript:") === 0) return;
    var body = new URLSearchParams();
    body.set("href", a.href);
    body.set("from", location.pathname);
    navigator.sendBeacon("/analytics/click", body);
  }, true);
})();
''')

write("Pages/Shared/_AdminNav.cshtml", r'''@{
    var path = (Context.Request.Path.Value ?? "").TrimEnd('/').ToLowerInvariant();
    var host = (Context.Request.Host.Host ?? "").ToLowerInvariant();
    var trader = host.Contains("pettrader") || host.Contains("ebaypet");
    bool onFound = path.EndsWith("/admin/found");
    bool onLost = path.EndsWith("/admin/lost");
    bool onHome = path.Equals("/admin") || path.Equals("/admin/index");
    bool onStats = path.StartsWith("/admin/analytics");
    var homeLabel = trader ? "PetTrader Admin" : "Admin";
}
<p class="mb-3 admin-nav">
    @if (onHome) { <strong>@homeLabel</strong> } else { <a href="/Admin">@homeLabel</a> }
    <span> · </span>
    @if (onStats) { <strong>Analytics</strong> } else { <a href="/Admin/Analytics">Analytics</a> }
    @if (!trader)
    {
        <span> · </span>
        @if (onFound) { <strong>Found pets</strong> } else { <a href="/Admin/Found">Found pets</a> }
        <span> · </span>
        @if (onLost) { <strong>Lost pets</strong> } else { <a href="/Admin/Lost">Lost pets</a> }
    }
</p>
''')

write("Pages/Admin/Analytics.cshtml", r'''@page
@model LostPet.Pages.Admin.AnalyticsModel
@{
    ViewData["Title"] = Model.Heading;
    ViewData["ChromeTitle"] = Model.Heading;
}
@{ await Html.RenderPartialAsync("_AdminNav"); }
<h1>@Model.Heading</h1>
<p class="text-muted">@(Model.Scope). This report starts when analytics was turned on. Older visits are not included. Your own visits count.</p>
<p>
    @if (Model.Days == 1) { <strong class="me-3">1 day</strong> } else { <a class="me-3" href="/Admin/Analytics?days=1">1 day</a> }
    @if (Model.Days == 7) { <strong class="me-3">7 days</strong> } else { <a class="me-3" href="/Admin/Analytics?days=7">7 days</a> }
    @if (Model.Days == 30) { <strong class="me-3">30 days</strong> } else { <a class="me-3" href="/Admin/Analytics?days=30">30 days</a> }
</p>
@if (!string.IsNullOrEmpty(Model.Error))
{
    <div class="alert alert-danger">@Model.Error</div>
}
<div class="row g-3 mb-4">
    <div class="col-6 col-md-3"><div class="border rounded p-3"><div class="text-muted small">Visits</div><div class="fs-3">@Model.Visits</div></div></div>
    <div class="col-6 col-md-3"><div class="border rounded p-3"><div class="text-muted small">Pages viewed</div><div class="fs-3">@Model.PageViews</div></div></div>
    <div class="col-6 col-md-3"><div class="border rounded p-3"><div class="text-muted small">Average time on a page</div><div class="fs-3">@Model.AvgTime</div></div></div>
    <div class="col-6 col-md-3"><div class="border rounded p-3"><div class="text-muted small">Most common last page</div><div class="fs-5">@Model.TopExit</div></div></div>
</div>

<h2 class="h4">How they got here</h2>
<p class="small text-muted">Typed address or bookmark means the browser did not name another site. Search engines usually hide the words that were searched.</p>
@if (Model.Arrivals.Count == 0)
{
    <p>No visits in this period yet. Open the public site in a private window, click a page or two, then refresh this report.</p>
}
else
{
    <table class="table table-sm">
        <thead><tr><th>Source</th><th>Visits</th><th style="width:40%"></th></tr></thead>
        <tbody>
        @foreach (var row in Model.Arrivals)
        {
            <tr>
                <td>@row.Label</td>
                <td>@row.Count</td>
                <td><div style="background:#e8f5e9;height:14px;border-radius:4px"><div style="width:@(row.Pct)%;height:14px;background:#2e7d32;border-radius:4px"></div></div></td>
            </tr>
        }
        </tbody>
    </table>
}

@if (Model.SearchTerms.Count > 0)
{
    <h2 class="h5">Search words</h2>
    <ul>
    @foreach (var row in Model.SearchTerms)
    {
        <li>@row.Label <span class="text-muted">(@row.Count)</span></li>
    }
    </ul>
}

<h2 class="h4 mt-4">Pages</h2>
<table class="table table-sm">
    <thead><tr><th>Page</th><th>Views</th><th>Average time</th><th>Left the site here</th></tr></thead>
    <tbody>
    @foreach (var row in Model.Pages)
    {
        <tr>
            <td>@row.Label</td>
            <td>@row.Count</td>
            <td>@row.Extra</td>
            <td>@row.Exits</td>
        </tr>
    }
    </tbody>
</table>

<h2 class="h4">Links they clicked</h2>
@if (Model.Clicks.Count == 0)
{
    <p class="text-muted">No clicks recorded yet. The page list above still shows which pages were opened.</p>
}
else
{
    <table class="table table-sm">
        <thead><tr><th>Link</th><th>Clicks</th></tr></thead>
        <tbody>
        @foreach (var row in Model.Clicks)
        {
            <tr><td style="word-break:break-all">@row.Label</td><td>@row.Count</td></tr>
        }
        </tbody>
    </table>
}

<h2 class="h4">Recent visits</h2>
@foreach (var visit in Model.Recent)
{
    <div class="border rounded p-2 mb-2">
        <div><strong>@visit.When</strong> · @visit.How</div>
        <div class="small">@visit.Trail</div>
    </div>
}
''')

write("Pages/Admin/Analytics.cshtml.cs", r'''using System.Data.Common;
using System.Globalization;
using LostPet.Data;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc.RazorPages;

namespace LostPet.Pages.Admin;

[Authorize(Roles = "Admin")]
public class AnalyticsModel : PageModel
{
    private readonly AppDbContext _db;
    public AnalyticsModel(AppDbContext db) { _db = db; }
    public int Days { get; set; } = 7;
    public bool Trader { get; set; }
    public string Heading => Trader ? "PetTrader Analytics" : "LostPet Analytics";
    public string Scope => Trader ? "pettrader.com and ebaypet.com" : "lostpet.com";
    public int Visits { get; set; }
    public int PageViews { get; set; }
    public string AvgTime { get; set; } = "n/a";
    public string TopExit { get; set; } = "n/a";
    public string? Error { get; set; }
    public List<StatRow> Arrivals { get; set; } = new();
    public List<StatRow> SearchTerms { get; set; } = new();
    public List<PageStat> Pages { get; set; } = new();
    public List<StatRow> Clicks { get; set; } = new();
    public List<RecentVisit> Recent { get; set; } = new();

    public async Task OnGetAsync(int days = 7)
    {
        Days = days == 1 || days == 30 ? days : 7;
        var host = (Request.Host.Host ?? "").ToLowerInvariant();
        Trader = host.Contains("pettrader") || host.Contains("ebaypet");
        Response.Headers["Cache-Control"] = "no-store";
        try
        {
            await LostPet.AnalyticsTracker.EnsureAsync(_db);
            var since = DateTime.UtcNow.AddDays(-Days);
            var hits = new List<Hit>();
            var conn = _db.Database.GetDbConnection();
            var opened = conn.State != System.Data.ConnectionState.Open;
            if (opened) await conn.OpenAsync();
            try
            {
                await using (var cmd = conn.CreateCommand())
                {
                    cmd.CommandText = "SELECT Id, SessionKey, Path, SourceType, SourceName, SearchTerms, StartedAt, DurationSeconds, IsExit, Host FROM SiteVisits WHERE StartedAt >= @since ORDER BY Id";
                    Add(cmd, "@since", since.ToString("yyyy-MM-ddTHH:mm:ssZ", CultureInfo.InvariantCulture));
                    await using var reader = await cmd.ExecuteReaderAsync();
                    while (await reader.ReadAsync())
                    {
                        var rowHost = reader.IsDBNull(9) ? "" : reader.GetString(9);
                        if (TraderHost(rowHost) != Trader) continue;
                        hits.Add(new Hit
                        {
                            Id = Convert.ToInt64(reader.GetValue(0)),
                            Session = reader.GetString(1),
                            Path = reader.GetString(2),
                            SourceType = reader.GetString(3),
                            SourceName = reader.IsDBNull(4) ? "" : reader.GetString(4),
                            Terms = reader.IsDBNull(5) ? null : reader.GetString(5),
                            At = DateTime.Parse(reader.GetString(6), CultureInfo.InvariantCulture, DateTimeStyles.AdjustToUniversal | DateTimeStyles.AssumeUniversal),
                            Seconds = reader.IsDBNull(7) ? null : Convert.ToInt32(reader.GetValue(7)),
                            Exit = Convert.ToInt32(reader.GetValue(8)) == 1
                        });
                    }
                }
                var clickMap = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
                await using (var cmd = conn.CreateCommand())
                {
                    cmd.CommandText = "SELECT Href, Host FROM SiteClicks WHERE ClickedAt >= @since";
                    Add(cmd, "@since", since.ToString("yyyy-MM-ddTHH:mm:ssZ", CultureInfo.InvariantCulture));
                    await using var reader = await cmd.ExecuteReaderAsync();
                    while (await reader.ReadAsync())
                    {
                        var rowHost = reader.IsDBNull(1) ? "" : reader.GetString(1);
                        if (TraderHost(rowHost) != Trader) continue;
                        var href = reader.GetString(0);
                        clickMap[href] = clickMap.TryGetValue(href, out var n) ? n + 1 : 1;
                    }
                }
                Clicks = clickMap.OrderByDescending(p => p.Value).Take(20)
                    .Select(p => new StatRow { Label = p.Key, Count = p.Value }).ToList();
            }
            finally { if (opened) await conn.CloseAsync(); }

            PageViews = hits.Count;
            var sessions = hits.GroupBy(h => h.Session).Select(g => g.OrderBy(h => h.Id).ToList()).ToList();
            Visits = sessions.Count;
            var timed = hits.Where(h => h.Seconds != null).Select(h => h.Seconds!.Value).ToList();
            AvgTime = timed.Count == 0 ? "n/a" : Fmt((int)Math.Round(timed.Average()));

            var arrivals = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
            var terms = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
            foreach (var session in sessions)
            {
                var first = session[0];
                var label = first.SourceType switch
                {
                    "Search" => "Search: " + first.SourceName,
                    "Referral" => "Backlink: " + first.SourceName,
                    "Internal" => "Already on the site",
                    _ => "Typed address or bookmark"
                };
                arrivals[label] = arrivals.TryGetValue(label, out var n) ? n + 1 : 1;
                if (first.SourceType == "Search" && !string.IsNullOrWhiteSpace(first.Terms))
                {
                    var key = first.SourceName + ": " + first.Terms;
                    terms[key] = terms.TryGetValue(key, out var t) ? t + 1 : 1;
                }
            }
            var maxA = arrivals.Count == 0 ? 1 : arrivals.Values.Max();
            Arrivals = arrivals.OrderByDescending(p => p.Value)
                .Select(p => new StatRow { Label = p.Key, Count = p.Value, Pct = (int)Math.Round(100.0 * p.Value / maxA) }).ToList();
            SearchTerms = terms.OrderByDescending(p => p.Value).Take(15)
                .Select(p => new StatRow { Label = p.Key, Count = p.Value }).ToList();

            var pages = new Dictionary<string, PageAcc>(StringComparer.OrdinalIgnoreCase);
            foreach (var h in hits)
            {
                var key = Pretty(h.Path);
                if (!pages.TryGetValue(key, out var acc)) pages[key] = acc = new PageAcc();
                acc.Views++;
                if (h.Seconds != null) { acc.Time += h.Seconds.Value; acc.Timed++; }
                if (h.Exit) acc.Exits++;
            }
            Pages = pages.OrderByDescending(p => p.Value.Views).Take(25).Select(p => new PageStat
            {
                Label = p.Key,
                Count = p.Value.Views,
                Exits = p.Value.Exits,
                Extra = p.Value.Timed == 0 ? "n/a" : Fmt((int)Math.Round((double)p.Value.Time / p.Value.Timed))
            }).ToList();
            var exit = pages.OrderByDescending(p => p.Value.Exits).FirstOrDefault();
            TopExit = exit.Value == null || exit.Value.Exits == 0 ? "n/a" : exit.Key;

            TimeZoneInfo az;
            try { az = TimeZoneInfo.FindSystemTimeZoneById("America/Phoenix"); }
            catch { az = TimeZoneInfo.Utc; }
            Recent = sessions.OrderByDescending(s => s[0].At).Take(25).Select(s =>
            {
                var first = s[0];
                var how = first.SourceType switch
                {
                    "Search" => "Search: " + first.SourceName + (string.IsNullOrWhiteSpace(first.Terms) ? "" : " \"" + first.Terms + "\""),
                    "Referral" => "Backlink: " + first.SourceName,
                    "Internal" => "Link on this site",
                    _ => "Typed address or bookmark"
                };
                var trail = string.Join("  >  ", s.Select(h => Pretty(h.Path) + " (" + (h.Seconds == null ? "n/a" : Fmt(h.Seconds.Value)) + (h.Exit ? ", left here" : "") + ")"));
                var local = TimeZoneInfo.ConvertTimeFromUtc(DateTime.SpecifyKind(first.At, DateTimeKind.Utc), az);
                return new RecentVisit { When = local.ToString("MMM d, h:mm tt", CultureInfo.InvariantCulture) + " Arizona", How = how, Trail = trail };
            }).ToList();
        }
        catch (Exception ex)
        {
            Error = ex.Message;
        }
    }

    static bool TraderHost(string host)
    {
        host = host.ToLowerInvariant();
        return host.Contains("pettrader") || host.Contains("ebaypet");
    }

    public static string Pretty(string path)
    {
        if (path.Equals("/PetTrader", StringComparison.OrdinalIgnoreCase) || path.Equals("/PetTrader/Index", StringComparison.OrdinalIgnoreCase))
            return "/ (home)";
        return path;
    }

    public static string Fmt(int seconds)
    {
        if (seconds < 60) return seconds + "s";
        return (seconds / 60) + "m " + (seconds % 60) + "s";
    }

    static void Add(DbCommand cmd, string name, object value)
    {
        var p = cmd.CreateParameter();
        p.ParameterName = name;
        p.Value = value;
        cmd.Parameters.Add(p);
    }

    class Hit
    {
        public long Id;
        public string Session = "";
        public string Path = "";
        public string SourceType = "";
        public string SourceName = "";
        public string? Terms;
        public DateTime At;
        public int? Seconds;
        public bool Exit;
    }
    class PageAcc { public int Views; public int Exits; public int Time; public int Timed; }
}

public class StatRow
{
    public string Label { get; set; } = "";
    public int Count { get; set; }
    public int Pct { get; set; }
}
public class PageStat
{
    public string Label { get; set; } = "";
    public int Count { get; set; }
    public int Exits { get; set; }
    public string Extra { get; set; } = "";
}
public class RecentVisit
{
    public string When { get; set; } = "";
    public string How { get; set; } = "";
    public string Trail { get; set; } = "";
}
''')

prog_path = root / "Program.cs"
backup(prog_path)
prog = prog_path.read_text(encoding="utf-8")
if "AnalyticsTracker" not in prog:
    if "app.UseAuthorization();" not in prog:
        raise SystemExit("NO AUTHORIZATION LINE")
    prog = prog.replace(
        "app.UseAuthorization();",
        "app.UseAuthorization();\napp.UseMiddleware<LostPet.AnalyticsTracker>();",
        1)
if "/analytics/beat" not in prog:
    idx = prog.rfind("app.Run();")
    if idx < 0:
        raise SystemExit("NO APP.RUN")
    prog = prog[:idx] + """app.MapPost("/analytics/beat", async (HttpContext ctx, AppDbContext db) =>
{
    await LostPet.AnalyticsTracker.BeatAsync(ctx, db);
    return Results.NoContent();
}).DisableAntiforgery();
app.MapPost("/analytics/click", async (HttpContext ctx, AppDbContext db) =>
{
    await LostPet.AnalyticsTracker.ClickAsync(ctx, db);
    return Results.NoContent();
}).DisableAntiforgery();

""" + prog[idx:]
prog_path.write_text(prog, encoding="utf-8")
print("program patched")

layout_path = root / "Pages/Shared/_Layout.cshtml"
backup(layout_path)
layout = layout_path.read_text(encoding="utf-8")
if "analytics.js?v=1" not in layout:
    i = layout.rfind("</body>")
    if i < 0:
        raise SystemExit("NO BODY TAG")
    layout = layout[:i] + '<script src="/js/analytics.js?v=1" defer></script>\n' + layout[i:]
cookie_old = "We use a login cookie and basic analytics-free storage so the site works."
cookie_new = "We use a login cookie and keep first-party visit stats on our own server."
if cookie_old in layout:
    layout = layout.replace(cookie_old, cookie_new, 1)
    print("cookie banner updated")
else:
    print("cookie banner text not found, left as-is")
mine = '<a class="btn btn-success btn-sm" href="/PetTrader/Mine">My listings</a>'
pets = '<a class="btn btn-success btn-sm" href="/Dashboard">My Pets</a>'
admin_btn = '\n@if (User.IsInRole("Admin")) { <a class="btn btn-outline-success btn-sm" href="/Admin">Admin</a> }'
if "IsInRole(\"Admin\")" not in layout:
    if mine in layout:
        layout = layout.replace(mine, mine + admin_btn, 1)
        print("pettrader layout admin button added")
    else:
        print("pettrader layout button NOT found")
    if pets in layout:
        layout = layout.replace(pets, pets + admin_btn, 1)
        print("lostpet layout admin button added")
    else:
        print("lostpet layout button NOT found")
else:
    print("layout admin button already present")
layout_path.write_text(layout, encoding="utf-8")
print("layout patched")

pt = root / "Pages/PetTrader/Index.cshtml"
if pt.exists():
    backup(pt)
    t = pt.read_text(encoding="utf-8")
    if "IsInRole(\"Admin\")" not in t and mine in t:
        t = t.replace(mine, mine + admin_btn, 1)
        pt.write_text(t, encoding="utf-8")
        print("pettrader home admin button added")
    else:
        print("pettrader home button skipped")

idx = root / "Pages/Admin/Index.cshtml"
if idx.exists():
    backup(idx)
    t = idx.read_text(encoding="utf-8")
    old = '{ ViewData["ChromeTitle"] = "Admin";\n    ViewData["Title"] = "Admin"; }'
    new = '''{
    var _admHost = (Request.Host.Host ?? "").ToLowerInvariant();
    var _admPt = _admHost.Contains("pettrader") || _admHost.Contains("ebaypet");
    ViewData["ChromeTitle"] = _admPt ? "PetTrader Admin" : "Admin";
    ViewData["Title"] = _admPt ? "PetTrader Admin" : "Admin";
}'''
    if old in t:
        t = t.replace(old, new, 1)
        t = t.replace("<h1>Admin</h1>", "<h1>@ViewData[\"Title\"]</h1>", 1)
        idx.write_text(t, encoding="utf-8")
        print("admin title patched")
    elif "PetTrader Admin" in t:
        print("admin title already patched")
    else:
        print("admin title pattern not found")

priv = root / "Pages/Privacy.cshtml"
if priv.exists():
    backup(priv)
    t = priv.read_text(encoding="utf-8")
    bullet = "<li>Server logs: IP address, page URL, time — used to run and protect the site</li>"
    add = bullet + "\n<li>First-party analytics: pages opened, how someone arrived (typed address, search, or a link from another site), links clicked, and time on a page. Kept on our server only. Not sold and not sent to an ad network.</li>"
    if "First-party analytics:" not in t and bullet in t:
        t = t.replace(bullet, add, 1)
        t = t.replace("Last updated September 9, 2026.", "Last updated October 1, 2026.")
        priv.write_text(t, encoding="utf-8")
        print("privacy updated")
    else:
        print("privacy skipped")

print("BUILDING")
r = subprocess.run(["dotnet", "publish", "-c", "Release", "-o", "/var/www/lostpet/app"], cwd=root)
print("PUBLISH_EXIT:" + str(r.returncode))
if r.returncode == 0:
    subprocess.run(["systemctl", "restart", "lostpet"])
    subprocess.run(["systemctl", "is-active", "lostpet"])
else:
    print("NOT RESTARTED because publish failed")
