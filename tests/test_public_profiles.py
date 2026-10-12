import io
import json
import re

from PIL import Image

from test_app import PASSWORD, numer, token_from
from test_profile import ProfileTestCase

import public_profiles as pub


def jpeg_bytes(size=(900, 600), color=(120, 60, 200), exif=True, fmt="JPEG"):
    image = Image.new("RGB", size, color)
    buffer = io.BytesIO()
    if exif and fmt == "JPEG":
        data = Image.Exif()
        data[0x010F] = "SecretCam"          # Make
        data[0x8825] = {2: (1.0, 2.0, 3.0)}  # GPSInfo
        image.save(buffer, fmt, exif=data.tobytes())
    else:
        image.save(buffer, fmt)
    return buffer.getvalue()


class PhotoProcessingTest(ProfileTestCase):
    def test_crops_to_square_and_strips_metadata(self):
        out = pub.process_photo(jpeg_bytes())
        image = Image.open(io.BytesIO(out))
        self.assertEqual(image.format, "JPEG")
        self.assertEqual(image.size, (512, 512))
        self.assertEqual(len(image.getexif()), 0)
        self.assertNotIn(b"SecretCam", out)

    def test_small_png_square(self):
        out = pub.process_photo(jpeg_bytes(size=(200, 300), fmt="PNG"))
        self.assertEqual(Image.open(io.BytesIO(out)).size, (512, 512))

    def test_rejects_non_images(self):
        for bad in (b"", b"not an image", b"<svg xmlns='http://www.w3.org/2000/svg'/>"):
            with self.assertRaises(ValueError):
                pub.process_photo(bad)


class PhotoUploadTest(ProfileTestCase):
    def upload(self, data, name="me.jpg"):
        token = token_from(self.client.get("/settings/profile").get_data(as_text=True))
        return self.client.post("/settings/photo", data={"csrf_token": token, "photo": (io.BytesIO(data), name)},
                                content_type="multipart/form-data")

    def test_requires_sign_in_and_csrf(self):
        self.assertIn(self.client.post("/settings/photo").status_code, (302, 400))
        self.login("liliana@example.com")
        response = self.client.post("/settings/photo", data={"photo": (io.BytesIO(jpeg_bytes()), "a.jpg")},
                                    content_type="multipart/form-data")
        self.assertEqual(response.status_code, 400)

    def test_upload_serve_and_remove(self):
        self.login("liliana@example.com")
        self.assertIsNone(pub_url(self))
        self.assertEqual(self.upload(jpeg_bytes()).status_code, 302)
        url = pub_url(self)
        self.assertRegex(url, rf"^/avatar/{self.practitioner_id}\.jpg\?v=[0-9a-f]{{12}}$")
        served = self.client.get(url)
        self.assertEqual(served.status_code, 200)
        self.assertEqual(served.mimetype, "image/jpeg")
        self.assertIn("immutable", served.headers["Cache-Control"])
        self.assertIn(url.split("?")[0], self.client.get("/settings/profile").get_data(as_text=True))
        token = token_from(self.client.get("/settings/profile").get_data(as_text=True))
        self.client.post("/settings/photo", data={"csrf_token": token, "remove": "yes"})
        self.assertIsNone(pub_url(self))
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_bad_file_shows_error_and_saves_nothing(self):
        self.login("liliana@example.com")
        response = self.upload(b"hello", "x.jpg")
        self.assertEqual(response.status_code, 302)
        self.assertIn("photo", self.client.get("/settings/profile").get_data(as_text=True).lower())
        self.assertIsNone(pub_url(self))

    def test_avatar_of_inactive_user_is_hidden(self):
        self.login("liliana@example.com")
        self.upload(jpeg_bytes())
        url = pub_url(self)
        with numer.open_db() as connection:
            connection.execute("UPDATE users SET is_active = 0 WHERE id = ?", (self.practitioner_id,))
        self.assertEqual(self.client.get(url).status_code, 404)


def pub_url(case):
    with numer.app.test_request_context():
        return pub.avatar_url(case.practitioner_id)


class AboutPageTest(ProfileTestCase):
    def fill_profile(self):
        self.login("liliana@example.com")
        self.save(headline="Certified numerologist", bio="I help people find their path.\nTwenty years of readings.",
                  booking_url="https://calendly.com/liliana", website="https://liliana.example.com",
                  instagram="@liliana.numbers")

    def test_public_author_page(self):
        self.fill_profile()
        response = self.client.get("/liliana/about")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("<h1>Liliana</h1>", html)
        self.assertIn("Certified numerologist", html)
        self.assertIn("Twenty years of readings.", html)
        self.assertIn('href="/liliana"', html)
        self.assertNotIn("\n", re.search(r'<meta name="description" content="(.*?)"', html, re.S).group(1))
        self.assertIn('rel="canonical" href="http://localhost/liliana/about"'.split(" href")[0], html)
        # every outbound or CTA link opens in a new tab
        for href in ("https://calendly.com/liliana", "https://liliana.example.com", "https://www.instagram.com/liliana.numbers"):
            for tag in re.findall(rf'<a [^>]*href="{re.escape(href)}"[^>]*>', html):
                self.assertIn('target="_blank"', tag)
                self.assertIn('rel="noopener"', tag)
        data = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S).group(1))
        self.assertEqual(data["@type"], "ProfilePage")
        self.assertEqual(data["mainEntity"]["name"], "Liliana")
        title = re.search(r"<title>(.*?)</title>", html, re.S).group(1)
        self.assertLessEqual(len(title), 60)
        description = re.search(r'<meta name="description" content="(.*?)"', html, re.S).group(1)
        self.assertLessEqual(len(description), 140)

    def test_unknown_or_inactive_practitioner_404(self):
        self.assertEqual(self.client.get("/nobody/about").status_code, 404)
        with numer.open_db() as connection:
            connection.execute("UPDATE users SET is_active = 0 WHERE id = ?", (self.practitioner_id,))
        self.assertEqual(self.client.get("/liliana/about").status_code, 404)

    def test_sitemap_lists_active_profiles_only(self):
        numer.create_user("gone@example.com", PASSWORD, "practitioner", "Gone", slug="gone")
        with numer.open_db() as connection:
            connection.execute("UPDATE users SET is_active = 0 WHERE slug = 'gone'")
        xml = self.client.get("/sitemap.xml").get_data(as_text=True)
        self.assertIn("/liliana/about</loc>", xml)
        self.assertNotIn("/gone/about", xml)

    def test_dashboard_and_profile_link_to_public_page_in_new_tab(self):
        self.login("liliana@example.com")
        for path in ("/dashboard", "/settings/profile"):
            html = self.client.get(path).get_data(as_text=True)
            tag = re.search(r'<a [^>]*href="[^"]*/liliana/about"[^>]*>', html)
            self.assertIsNotNone(tag, path)
            self.assertIn('target="_blank"', tag.group(0))


class ReviewFollowupTest(ProfileTestCase):
    def test_json_ld_cannot_break_out_of_its_script_tag(self):
        self.login("liliana@example.com")
        self.save(headline="</script><meta http-equiv='refresh' content='0;url=https://evil.example'>")
        html = self.client.get("/liliana/about").get_data(as_text=True)
        script = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S).group(1)
        self.assertNotIn("<", script)
        self.assertEqual(json.loads(script)["mainEntity"]["jobTitle"][:9], "</script>")

    def test_rejects_huge_dimensions_before_decoding(self):
        image = Image.new("1", (6000, 6000))  # 36 MP, tiny when compressed
        buffer = io.BytesIO()
        image.save(buffer, "PNG")
        self.assertLess(len(buffer.getvalue()), 100_000)
        with self.assertRaises(ValueError):
            pub.process_photo(buffer.getvalue())

    def test_no_avatar_url_for_inactive_practitioner(self):
        data = pub.process_photo(jpeg_bytes())
        with numer.open_db() as connection:
            connection.execute("INSERT INTO avatars (user_id, image, version, updated_at) VALUES (?, ?, 'abc', 'x')",
                               (self.practitioner_id, data))
        self.assertIsNotNone(pub_url(self))
        with numer.open_db() as connection:
            connection.execute("UPDATE users SET is_active = 0 WHERE id = ?", (self.practitioner_id,))
        self.assertIsNone(pub_url(self))
