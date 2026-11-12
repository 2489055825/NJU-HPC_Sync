def test_preview_counts_all_changes_before_sample_truncation():
    from app.sync_service import summarize

    output = (">f+++++++++ new-file\n" * 120 + ">f.st...... updated-file\n" * 30
              + "cd+++++++++ new-directory/\n.d..t...... old-directory/\n"
              + "Number of regular files transferred: 150\nNumber of deleted files: 2\n")
    result = summarize(output)
    assert result["summary"]["added_entries"] == 121
    assert result["summary"]["updated_entries"] == 31
    assert result["summary"]["deleted_entries"] == 2
    assert len(result["changes"]) == 100 and result["changes_truncated"]


def test_missing_statistics_are_not_fabricated_zero_counts():
    from app.sync_service import summarize

    result = summarize("rsync: connection failed\n")
    assert result["summary"]["added_entries"] is None
    assert result["summary"]["updated_entries"] is None
