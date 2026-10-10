package routes

import "testing"

func TestRemoveRecoverySectionPreservesOtherOpenRCSettings(t *testing.T) {
	before := "rc_need=\"net\"\n# BEGIN CONFIGFLOW RECOVERY mihomo\nrc_need=\"${rc_need:-} configflow-recover-mihomo\"\n# END CONFIGFLOW RECOVERY mihomo\ncustom=\"keep\"\n"
	expected := "rc_need=\"net\"\ncustom=\"keep\"\n"
	if got := removeRecoverySection(before, "mihomo"); got != expected {
		t.Fatalf("cleanup = %q", got)
	}
	if got := removeRecoverySection(before, "mosdns"); got != before {
		t.Fatal("modified unrelated service")
	}
	broken := "keep=1\n# BEGIN CONFIGFLOW RECOVERY mihomo\nother=2\n"
	if got := removeRecoverySection(broken, "mihomo"); got != broken {
		t.Fatal("removed malformed user configuration")
	}
}
