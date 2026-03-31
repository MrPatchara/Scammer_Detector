import json
import os
import tempfile
import unittest
from pathlib import Path

from app.config import reload_runtime_config
from app.risk import calculate_risk, keyword_detect


RANK = {"low": 0, "medium": 1, "high": 2}


class TestRiskScoringCases(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.pop("SCAM_KEYWORDS_FILE", None)
        reload_runtime_config()

    def assert_min_risk(self, text: str, min_level: str, min_probability: float) -> None:
        keywords = keyword_detect(text)
        risk = calculate_risk(text, keywords)
        self.assertGreaterEqual(RANK[risk["risk_level"]], RANK[min_level], msg=text)
        self.assertGreaterEqual(risk["probability"], min_probability, msg=text)

    def test_26_sample_cases(self) -> None:
        cases = [
            ("ผมเป็นเจ้าหน้าที่ธนาคาร กรุณาโอนเงินทันทีและแจ้ง otp", "high", 0.70),
            ("ตำรวจ dsi แจ้งว่าบัญชีคุณพัวพันคดี ให้โอนค่าปรับเดี๋ยวนี้", "high", 0.70),
            ("พัสดุผิดกฎหมาย หากไม่ดำเนินการจะฟ้องร้อง ให้กดลิงก์ติดตั้งแอป", "high", 0.68),
            ("call center จากธนาคาร ขอเลขบัตรประชาชนและรหัสยืนยันเพื่อปลดล็อกบัญชี", "high", 0.72),
            ("ลงทุนผลตอบแทนสูง ปันผลรายวัน โอนมัดจำก่อนเพื่อเปิดพอร์ต", "medium", 0.40),
            ("งานออนไลน์ได้เงินจริง ภารกิจกดรับออเดอร์ เติมเงินก่อนแล้วถอนกำไร", "medium", 0.40),
            ("ช่วยติดตั้ง anydesk แล้วแชร์หน้าจอเพื่อแก้ปัญหาบัญชี กรุณารีบ", "high", 0.50),
            ("refund คืนภาษีด่วน คลิกลิงก์แล้วกรอกเลขบัตรประชาชน", "high", 0.68),
            ("เจ้าหน้าที่การไฟฟ้าแจ้งค้างชำระ มิฉะนั้นตัดไฟทันที ให้โอนเงิน", "high", 0.67),
            ("พัสดุติดศุลกากร ต้องชำระค่าปลดปล่อยพัสดุภายในวันนี้", "medium", 0.38),
            ("บัญชีคุณผิดปกติ กรุณายืนยันตัวตนภายในวันนี้", "medium", 0.48),
            ("อนุมัติสินเชื่อด่วน ขอรูปบัตรประชาชนและเลขหลังบัตร", "high", 0.66),
            ("ฝ่ายกฎหมายแจ้งหมายจับ ต้องคุยลับและห้ามบอกใคร", "medium", 0.30),
            ("เจ้าหน้าที่ศูนย์บริการ ขอให้กดลิงก์เพื่อตรวจสอบบัญชี", "medium", 0.55),
            ("โทรมายืนยันการชำระ invoice ค้างชำระ กรุณารีบดำเนินการ", "medium", 0.35),
            (
                "สวัดดีครับ ผมโทรมาเรื่องบัญชีของคุณ ช่วยโอนมาเข้าบันชีเพื่อตรวสอบตามที่บอกทันที",
                "medium",
                0.35,
            ),
            (
                "สวัดดีครับผมจะนาที่ตำรับรับรับพบว่าคุณมีการกระทำความษิดมีการโน่น จะมลุนมาฝันบันชีของคุณช่วยการโน่นาทามที่ผมบอกด้วยคุณจะต้องโน่นมายงบันชีพือตรวสอบครับ",
                "medium",
                0.25,
            ),
            ("วันนี้ฝนตกหนัก รถติดมาก กลับบ้านช้านิดหน่อย", "low", 0.01),
            ("ประชุมทีมพรุ่งนี้สิบโมง เตรียมสไลด์ยอดขายไตรมาสแรก", "low", 0.01),
            ("อย่าลืมซื้อของเข้าบ้าน นม ไข่ ขนมปัง", "low", 0.01),
            ("ขอบคุณที่ช่วยตรวจเอกสารเมื่อวาน งานเรียบร้อยแล้ว", "low", 0.01),
            ("ธนาคารเปิดทำการตามปกติในวันจันทร์ถึงศุกร์", "low", 0.01),
            ("ลูกค้าต้องการใบเสนอราคาและกำหนดส่งของ", "low", 0.01),
            ("แจ้งเตือนระบบจะปิดปรับปรุงคืนนี้ เวลาเที่ยงคืนถึงตีสอง", "low", 0.01),
            ("กรุณารีเซ็ตรหัสผ่านอีเมลบริษัทตามรอบความปลอดภัย", "low", 0.01),
            ("พรุ่งนี้ไปวิ่งสวนลุมตอนเช้าแล้วค่อยทำงาน", "low", 0.01),
            ("โครงการนี้ต้องส่งรายงานสิ้นเดือนและตรวจคุณภาพข้อมูล", "low", 0.01),
            ("ทีมบัญชีสรุปค่าใช้จ่ายประจำเดือนและปิดงบแล้ว", "low", 0.01),
        ]

        for text, min_level, min_probability in cases:
            with self.subTest(text=text):
                self.assert_min_risk(text, min_level, min_probability)


class TestExternalKeywordConfig(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("SCAM_KEYWORDS_FILE", None)
        reload_runtime_config()

    def test_default_external_keywords_loaded(self) -> None:
        os.environ.pop("SCAM_KEYWORDS_FILE", None)
        reload_runtime_config()

        text = "refund คืนภาษีด่วน และมีหมายจับ"
        found = keyword_detect(text)
        self.assertIn("refund", found)
        self.assertIn("หมายจับ", found)

    def test_custom_external_file_override(self) -> None:
        payload = {
            "replace": True,
            "keywords": ["xscam", "xurgent"],
            "categories": {
                "urgency": ["xurgent"],
                "financial": ["xmoney"],
                "sensitive": [],
                "impersonation": [],
                "link_install": [],
                "legal_threat": [],
            },
            "weights": {
                "base_keyword": 5,
                "keyword_cap": 30,
                "urgency": 20,
                "financial": 10,
                "medium_threshold": 20,
                "high_threshold": 50,
                "probability_mix": 0.2,
            },
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "custom_keywords.json"
            config_path.write_text(json.dumps(payload), encoding="utf-8")

            os.environ["SCAM_KEYWORDS_FILE"] = str(config_path)
            reload_runtime_config()

            text = "xscam xurgent xmoney"
            found = keyword_detect(text)
            risk = calculate_risk(text, found)

            self.assertIn("xscam", found)
            self.assertIn("xurgent", found)
            self.assertGreaterEqual(RANK[risk["risk_level"]], RANK["medium"])


if __name__ == "__main__":
    unittest.main()
