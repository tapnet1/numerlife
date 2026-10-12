import json
import re
from unittest import mock

from test_app import AppTestCase, numer
from test_intake import IntakeTestCase

import content


def structured_data(html):
    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    return [json.loads(block) for block in blocks]


class HomeSeoTest(AppTestCase):
    def test_title_description_and_social_tags(self):
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn("<title>Free Numerology Report: Your Life Path Number | numer.life</title>", html)
        self.assertIn('name="keywords" content="numerology, free numerology report, life path number', html)
        for tag in ('name="description"', 'property="og:title"', 'property="og:description"',
                    'property="og:image" content="http://localhost/static/img/og-image.jpg"',
                    'property="og:image:width" content="1200"', 'name="twitter:card" content="summary_large_image"',
                    'rel="canonical" href="http://localhost/"', 'rel="apple-touch-icon"'):
            self.assertIn(tag, html)

    def test_title_and_description_fit_search_limits(self):
        import html as html_lib
        for path in ("/", "/privacy"):
            page = self.client.get(path).get_data(as_text=True)
            title = html_lib.unescape(re.search(r"<title>(.*?)</title>", page, re.S).group(1).strip())
            description = html_lib.unescape(re.search(r'name="description" content="([^"]*)"', page).group(1))
            self.assertLessEqual(len(title), 60, title)
            self.assertLessEqual(len(description), 140, description)

    def test_og_image_and_fonts_are_served(self):
        for path in ("/static/img/og-image.jpg", "/static/img/apple-touch-icon.png",
                     "/static/fonts/cormorant-garamond-latin-700-normal.woff2", "/static/js/share.js"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            response.close()

    def test_structured_data_matches_visible_faq(self):
        html = self.client.get("/").get_data(as_text=True)
        graph = structured_data(html)[0]["@graph"]
        types = {node["@type"] for node in graph}
        self.assertEqual(types, {"WebSite", "WebApplication", "FAQPage"})
        faq = next(node for node in graph if node["@type"] == "FAQPage")
        self.assertEqual(len(faq["mainEntity"]), len(content.FAQS))
        for question in faq["mainEntity"]:
            self.assertIn(question["name"].replace("'", "&#39;"), html)

    def test_educational_content_is_on_the_page(self):
        html = self.client.get("/").get_data(as_text=True)
        for text in ("What is numerology?", "The five core numbers", "Pythagorean letter values",
                     "Master numbers 11, 22 and 33", "Life Path number meanings", "The Master Teacher"):
            self.assertIn(text, html)
        self.assertEqual(len(re.findall(r"<h1[ >]", html)), 1)

    def test_no_public_sign_in_link(self):
        html = self.client.get("/").get_data(as_text=True)
        self.assertNotIn("Practitioner sign in", html)
        self.assertNotIn('href="/login"', html)

    def test_share_bar_links_home(self):
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn('data-share-url="http://localhost/"', html)
        self.assertIn("https://wa.me/?text=", html)
        self.assertIn("facebook.com/sharer/sharer.php?u=http%3A//localhost/", html)

    def test_sitemap_and_robots_after_launch(self):
        sitemap = self.client.get("/sitemap.xml")
        self.assertEqual(sitemap.status_code, 200)
        self.assertIn(b"<loc>http://localhost/</loc>", sitemap.data)
        with mock.patch.object(numer, "NOINDEX", False):
            robots = self.client.get("/robots.txt").get_data(as_text=True)
            html = self.client.get("/").get_data(as_text=True)
        self.assertIn("Sitemap: http://localhost/sitemap.xml", robots)
        self.assertIn("Disallow: /r/", robots)
        self.assertIn('content="index, follow, max-image-preview:large"', html)


class PractitionerSeoAndShareTest(IntakeTestCase):
    def test_practitioner_page_is_canonical_to_home_and_shares_their_link(self):
        html = self.client.get("/liliana").get_data(as_text=True)
        self.assertIn('rel="canonical" href="http://localhost/"', html)
        self.assertIn("Free Numerology Report from Liliana", html)
        self.assertIn('data-share-url="http://localhost/liliana"', html)

    def test_practitioner_title_stays_within_limit(self):
        import html as html_lib
        with numer.open_db() as connection:
            connection.execute("UPDATE users SET display_name = ? WHERE id = ?",
                               ("Liliana Alexandra Montgomery-Fitzwilliam", self.practitioner_id))
        page = self.client.get("/liliana").get_data(as_text=True)
        title = html_lib.unescape(re.search(r"<title>(.*?)</title>", page, re.S).group(1).strip())
        self.assertLessEqual(len(title), 60, title)

    def test_share_keeps_the_practitioner_link_after_browsing(self):
        self.client.get("/liliana")
        for path in ("/", "/privacy"):
            self.client.get(path)
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn('data-share-url="http://localhost/liliana"', html)
        token = self.submit(path="/").headers["Location"].rsplit("/", 1)[-1]
        report = self.client.get(f"/r/{token}").get_data(as_text=True)
        self.assertIn('data-share-url="http://localhost/liliana"', report)

    def test_friend_opening_a_shared_link_shares_the_same_link(self):
        friend = numer.app.test_client()
        html = friend.get("/liliana").get_data(as_text=True)
        self.assertIn('data-share-url="http://localhost/liliana"', html)

    def test_report_page_never_shares_its_own_private_link(self):
        token = self.submit(path="/liliana").headers["Location"].rsplit("/", 1)[-1]
        html = self.client.get(f"/r/{token}").get_data(as_text=True)
        share = re.search(r'data-share-url="([^"]+)"', html).group(1)
        self.assertEqual(share, "http://localhost/liliana")
        share_section = html[html.index('class="share"'):]
        self.assertNotIn(token, share_section)
