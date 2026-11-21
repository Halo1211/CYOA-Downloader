"""Export BeautifulSoup(html, "html.parser") round-trip serialization (str(soup))
for parity of the Rust HTML tree. Run from the repository root."""
import json
from pathlib import Path
import bs4
from bs4 import BeautifulSoup

cases = [
    "<!DOCTYPE html><html><head><title>T</title></head><body><p>Hi</p></body></html>",
    "<!doctype html>\n<html lang=en>\n<head>\n<meta charset=utf-8>\n<link rel=stylesheet href=css/a.css>\n</head>\n<body>\n</body>\n</html>\n",
    "<DIV CLASS='a' Data-X=\"1\">Text</DIV>",
    "<img src=a.png alt=\"say &quot;hi&quot; &amp; bye\"><br><hr/><input disabled>",
    "<p>a &amp; b &lt; c &gt; d &copy; &#169; &#xA9; &nbsp;x</p>",
    "<script>if (a < b && c > d) { x = '</div>'; }</script><style>a>b{c:d}</style>",
    "<!-- comment --><p>x<!--inner--></p>",
    "<a href='x?a=1&b=2'>q</a><a href=\"x?a=1&amp;b=2\">r</a>",
    "<ul><li>one<li>two</ul>",
    "<p>one<p>two",
    "<div><span>unclosed</div>after",
    "<table><tr><td>a<td>b</tr></table>",
    "<select><option>a<option selected>b</select>",
    "<textarea>  <b>raw?</b> </textarea><pre>\n  x</pre>",
    "<svg viewBox=\"0 0 1 1\"><path d=\"M0 0\"/><circle r=1></circle></svg>",
    "<p title=\"it's\">x</p><p title='say \"q\"'>y</p>",
    "<p a=1 a=2>dup</p>",
    "text before<html><body>x</body></html>text after",
    "</p>stray close<br></br>",
    "<p>unicode ü ✓ 😀 &unknown; &amp</p>",
    "<![CDATA[x]]><?php echo 1; ?><p>x</p>",
    "<input value=\"a\nb\"><img src=\"  spaced.png  \">",
    "<noscript><img src=n.png></noscript><template><p>t</p></template>",
    "<body onload=\"init()\"><div id=x class=\"a  b\"></div></body>",
    "<p>\r\nwindows\r\nlines</p>",
    "<iframe src=\"f.html\"></iframe><video src=v.mp4 controls autoplay></video><source srcset=\"a.png 1x, b.png 2x\">",
    "<a href=#top>t</a><base href=\"/root/\"><link rel=preload as=font href=f.woff2 crossorigin>",
    "<html><head></head><body><script type=module src=m.js></script><script>var s=\"<\\/script>\";</script></body></html>",
    "<P><B>bold<I>both</B>italic</I></P>",
    "<meta http-equiv=\"refresh\" content=\"0; url=next.html\"><title>a &amp; b</title>",
    "",
    "plain text only & more",
    "<meta charset=iso-8859-1><meta http-equiv=Content-Type content=\"text/html; charset=windows-1252\"><meta http-equiv=x content=\"charset=q\">",
    "<p>&#150; &#0; &#xD800; &#1114112; &#x110000; &#xFDD0; &#127; &#x41 &#65x &#; &#x;</p>",
    "<!----><p> </p><div>\n\n</div><pre>  </pre><textarea>\t</textarea><span>\u00a0</span>",
    "<a class=' x  y ' rel='nofollow  noopener' rev=a href=h>a</a><td headers='h1 h2'>c</td><div class=''>e</div><div class>f</div>",
    "<!foo bar><!><p>x</p></><  p>a<b</p>",
    "<a href=x/><br/ ><p  x = \"1\"  y>z</p><P X=1 x=2>w</P>",
    "<p>&AMP; &Amp; &lt &gt; &quot; &apos; &notin; &notit; &ampx</p>",
    "<div class=\"x",
    "<p>x<!-- unterminated",
    "<script>var x = 1;",
    "<style>a{}</STYLE ><script>a</script foo>b",
    "<title>a<b>c</b></title><p>&lt;b&gt;</p>",
    "<rt>r</rt><rp>(</rp><template><b>t &amp; u</b></template>",
    "<?xml version=\"1.0\"?><p>x</p><!DOCTYPE html PUBLIC \"-//W3C//DTD XHTML 1.0//EN\">",
    "<![if !IE]><p>x</p><![endif]><![CDATA[a]]b]]>",
    "<img src=\"a.png\" alt='x' title=\"y'z\" data-q='\"'><img alt=\"&lt;&gt;\">",
]
import bs4.dammit as dm
win = {k: v.decode("utf8") for k, v in dm.UnicodeDammit.WINDOWS_1252_TO_UTF8.items() if 0x80 <= k <= 0x9f}
(Path(__file__).parent / "website" / "win1252.json").write_text(json.dumps({"map": {str(k): v for k, v in win.items()}, "nonchars": sorted(dm.UnicodeDammit.ENUMERATED_NONCHARACTERS)}, ensure_ascii=False), encoding="utf-8")
out = [[c, str(BeautifulSoup(c, "html.parser"))] for c in cases]
p = Path(__file__).parent / "website" / "html_roundtrip.json"
p.write_text(json.dumps({"bs4": bs4.__version__, "cases": out}, ensure_ascii=False, indent=1), encoding="utf-8")
for src, dst in []:
    if src != dst:
        print(repr(src)[:70], "=>", repr(dst)[:110])
