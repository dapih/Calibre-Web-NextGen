// SPDX-License-Identifier: GPL-3.0-or-later
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;

internal sealed class WindowsRelease
{
    internal readonly string Tag;
    internal readonly Version Number;
    internal readonly Dictionary<string, object> Asset;

    internal WindowsRelease(string tag, Dictionary<string, object> asset)
    {
        Tag = tag;
        Number = WindowsReleases.ParseTag(tag);
        Asset = asset;
    }

    internal bool IsZip
    {
        get { return ((string)Asset["name"]).EndsWith(".zip", StringComparison.Ordinal); }
    }

    internal bool CanUpdate
    {
        get
        {
            object digest;
            return IsZip && Number != null && Convert.ToInt64(Asset["size"]) > 0 &&
                   Asset.TryGetValue("digest", out digest) && digest is string &&
                   Regex.IsMatch((string)digest, @"^sha256:[0-9a-fA-F]{64}$");
        }
    }

    internal string DisplayVersion
    {
        get { return "v" + Number.ToString(3) + (Number.Revision == 0 ? "" : "-r" + Number.Revision); }
    }
}

internal static class WindowsReleases
{
    private const string ReleaseApi = "https://api.github.com/repos/dapih/Calibre-Web-NextGen/releases?per_page=100";

    internal static WindowsRelease[] Published()
    {
        ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12;
        var request = (HttpWebRequest)WebRequest.Create(ReleaseApi);
        request.UserAgent = "CWNG-Windows";
        request.Accept = "application/vnd.github+json";
        request.Timeout = request.ReadWriteTimeout = 15000;
        using (WebResponse response = request.GetResponse())
        using (var reader = new StreamReader(response.GetResponseStream()))
            return ParsePublished(reader.ReadToEnd());
    }

    internal static WindowsRelease[] ParsePublished(string json)
    {
        var serializer = new JavaScriptSerializer { MaxJsonLength = 8 * 1024 * 1024 };
        var releases = serializer.Deserialize<Dictionary<string, object>[]>(json);
        return releases.Where(item => !(bool)item["draft"] && !(bool)item["prerelease"])
            .OrderByDescending(item => (string)item["published_at"], StringComparer.Ordinal)
            .Select(item => new WindowsRelease((string)item["tag_name"], FindPayload(item)))
            .Where(release => release.Asset != null)
            .ToArray();
    }

    internal static WindowsRelease FindUpdate(IEnumerable<WindowsRelease> releases, Version installed)
    {
        if (installed == null) return null;
        return releases.Where(release => release.CanUpdate && release.Number > installed)
            .OrderByDescending(release => release.Number).FirstOrDefault();
    }

    internal static Version ParseTag(string tag)
    {
        var match = Regex.Match(tag, @"^(?:cwng-)?windows-v([0-9]+\.[0-9]+\.[0-9]+)(?:-r([1-9][0-9]*))?\z");
        if (!match.Success || (!match.Groups[2].Success && tag.StartsWith("cwng-", StringComparison.Ordinal)))
            return null;
        Version version;
        return Version.TryParse(match.Groups[1].Value + "." +
            (match.Groups[2].Success ? match.Groups[2].Value : "0"), out version) ? version : null;
    }

    internal static Version InstalledVersion(string root)
    {
        string receiptPath = Path.Combine(root, WindowsInstallation.InstalledReceipt);
        string versionPath = Path.Combine(root, "VERSION");
        if (!File.Exists(receiptPath) || !File.Exists(versionPath)) return null;

        Version application;
        if (!Version.TryParse(File.ReadAllText(versionPath).Trim(), out application) ||
            application.Build < 0 || application.Revision >= 0) return null;
        string receipt = File.ReadAllText(receiptPath).Trim();
        // An older installer left an empty receipt. Only offer a newer app version
        // until an installation records its exact packaging revision.
        if (receipt.Length == 0) return new Version(application.Major, application.Minor, application.Build, int.MaxValue);
        Version installed = ParseTag(receipt);
        return installed != null && installed.ToString(3) == application.ToString(3) ? installed : null;
    }

    private static Dictionary<string, object> FindPayload(Dictionary<string, object> release)
    {
        string tag = (string)release["tag_name"];
        bool versioned = tag.StartsWith("cwng-windows-v", StringComparison.Ordinal);
        if (!versioned && !tag.StartsWith("windows-v", StringComparison.Ordinal)) return null;
        var assets = ((System.Collections.IEnumerable)release["assets"]).Cast<Dictionary<string, object>>();
        if (versioned) return assets.FirstOrDefault(asset => IsPayload(asset, tag + ".zip"));
        return assets.FirstOrDefault(asset => IsPayload(asset, "cwng-windows.zip")) ??
               assets.FirstOrDefault(asset => IsPayload(asset, "CWNG.exe"));
    }

    private static bool IsPayload(Dictionary<string, object> asset, string name)
    {
        return (string)asset["name"] == name && (string)asset["state"] == "uploaded";
    }
}
