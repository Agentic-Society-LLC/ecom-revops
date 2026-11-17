import os, sys, tempfile, unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import connectors  # noqa: E402


class Env(unittest.TestCase):
    def test_inline_comments_and_blanks_are_not_values(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, ".env")
            with open(p, "w") as f:
                f.write("TEST_A=        # a comment\nTEST_B=abc   # note\nTEST_C=\"q\"\n# TEST_D=x\n")
            for k in ("TEST_A", "TEST_B", "TEST_C", "TEST_D"):
                os.environ.pop(k, None)
            connectors.load_env(p)
            self.assertNotIn("TEST_A", os.environ)
            self.assertEqual(os.environ["TEST_B"], "abc")
            self.assertEqual(os.environ["TEST_C"], "q")
            self.assertNotIn("TEST_D", os.environ)

    def test_example_env_configures_nothing(self):
        example = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env.example")
        with open(example) as f:
            keys = [l.split("=")[0] for l in f if "=" in l and not l.startswith("#")]
        for k in keys:
            os.environ.pop(k, None)
        connectors.load_env(example)
        # only the placeholder store name is set; no connector becomes configured from the example alone
        self.assertFalse(any(c.configured() for c in connectors.REGISTRY))
        os.environ.pop("SHOPIFY_STORE", None)


if __name__ == "__main__":
    unittest.main()
