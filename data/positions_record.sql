BEGIN TRANSACTION;
CREATE TABLE pos(
      id INTEGER PRIMARY KEY, symbol TEXT, cluster TEXT, status TEXT,
      queued_on TEXT, entry_day TEXT, entry_px REAL, qty INTEGER,
      stop REAL, target REAL, exit_day TEXT, exit_px REAL,
      exit_reason TEXT, net REAL, features TEXT, fill_source TEXT, bucket TEXT DEFAULT 'main', origin TEXT);
INSERT INTO "pos" VALUES(1,'YUKEN','micro','open','2026-08-18','2026-08-19',900.0,51,810.0,1080.0,NULL,NULL,NULL,NULL,NULL,'confirmed','main',NULL);
INSERT INTO "pos" VALUES(2,'HAPPYFORGE','small','void','2026-08-18','2026-08-19',2280.0,19,2052.0,2736.0,'2026-08-19',NULL,'void: duplicate entry in a name already open since 2026-08-17; the pbook->positions rename lost the original, so dedup could not see it',NULL,NULL,'confirmed','main',NULL);
INSERT INTO "pos" VALUES(3,'HAPPYFORGE','small','open','2026-08-14','2026-08-17',2131.2,21,1918.08,2.557439999999999599e+03,NULL,NULL,NULL,NULL,'{"rs": 0.6005971018714047, "deliv": 52.42606557377049, "liq": 120163000.00000001, "off_high": 0.4393513905245163, "near_high": -0.4393513905245163, "rsi": null}',NULL,'main',NULL);
INSERT INTO "pos" VALUES(4,'GMMPFAUDLR','small','open','2026-08-17','2026-08-18',1053.0,42,947.7,1263.6,NULL,NULL,NULL,NULL,NULL,'confirmed','main','rank-cohort');
INSERT INTO "pos" VALUES(5,'SAHYADRI','micro','open','2026-08-17','2026-08-18',388.0,115,349.2,4.655999999999999659e+02,NULL,NULL,NULL,NULL,NULL,'confirmed','main','rank-cohort');
INSERT INTO "pos" VALUES(6,'VCL','micro','void','2026-08-19',NULL,NULL,23195,1.75,2.33,'2026-08-19',NULL,'void: the 2026-08-19 trigger bar was an upper circuit lock (O=H=L=C 1.94, +4.86%, 129 trades, 9 of the last 20 bars locked). The breakout high IS the price band, and at an upper lock there are no sellers, so the next-open fill this order assumes cannot be got. engine.gate() has always rejected high==low but nothing called it; the guard now lives in selection.build and VCL is no longer triggered.',NULL,NULL,NULL,'main',NULL);
CREATE TABLE pos_log(
      seq INTEGER PRIMARY KEY,
      at TEXT NOT NULL DEFAULT (datetime('now')),
      pos_id INTEGER NOT NULL, action TEXT NOT NULL, row TEXT NOT NULL);
INSERT INTO "pos_log" VALUES(1,'2026-08-19 05:37:29',2,'update','{"id":2,"symbol":"HAPPYFORGE","cluster":"small","status":"void","queued_on":"2026-08-18","entry_day":"2026-08-19","entry_px":2280.0,"qty":19,"stop":2052.0,"target":2736.0,"exit_day":"2026-08-19","exit_px":null,"exit_reason":"void: duplicate entry in a name already open since 2026-08-17; the pbook->positions rename lost the original, so dedup could not see it","net":null,"features":null,"fill_source":"live","bucket":"main","origin":null}');
INSERT INTO "pos_log" VALUES(2,'2026-08-19 05:37:29',3,'insert','{"id":3,"symbol":"HAPPYFORGE","cluster":"small","status":"open","queued_on":"2026-08-14","entry_day":"2026-08-17","entry_px":2131.2,"qty":21,"stop":1918.08,"target":2557.44,"exit_day":null,"exit_px":null,"exit_reason":null,"net":null,"features":"{\"rs\": 0.6005971018714047, \"deliv\": 52.42606557377049, \"liq\": 120163000.00000001, \"off_high\": 0.4393513905245163, \"near_high\": -0.4393513905245163, \"rsi\": null}","fill_source":null,"bucket":"main","origin":null}');
INSERT INTO "pos_log" VALUES(3,'2026-08-19 05:37:29',4,'insert','{"id":4,"symbol":"GMMPFAUDLR","cluster":"small","status":"open","queued_on":"2026-08-17","entry_day":"2026-08-18","entry_px":1053.0,"qty":42,"stop":947.7,"target":1263.6,"exit_day":null,"exit_px":null,"exit_reason":null,"net":null,"features":null,"fill_source":"confirmed","bucket":"main","origin":"rank-cohort"}');
INSERT INTO "pos_log" VALUES(4,'2026-08-19 05:37:29',5,'insert','{"id":5,"symbol":"SAHYADRI","cluster":"micro","status":"open","queued_on":"2026-08-17","entry_day":"2026-08-18","entry_px":388.0,"qty":115,"stop":349.2,"target":465.6,"exit_day":null,"exit_px":null,"exit_reason":null,"net":null,"features":null,"fill_source":"confirmed","bucket":"main","origin":"rank-cohort"}');
INSERT INTO "pos_log" VALUES(5,'2026-08-19 12:40:24',1,'update','{"id":1,"symbol":"YUKEN","cluster":"micro","status":"open","queued_on":"2026-08-18","entry_day":"2026-08-19","entry_px":900.0,"qty":51,"stop":810.0,"target":1080.0,"exit_day":null,"exit_px":null,"exit_reason":null,"net":null,"features":null,"fill_source":"confirmed","bucket":"main","origin":null}');
INSERT INTO "pos_log" VALUES(6,'2026-08-19 12:40:24',2,'update','{"id":2,"symbol":"HAPPYFORGE","cluster":"small","status":"void","queued_on":"2026-08-18","entry_day":"2026-08-19","entry_px":2280.0,"qty":19,"stop":2052.0,"target":2736.0,"exit_day":"2026-08-19","exit_px":null,"exit_reason":"void: duplicate entry in a name already open since 2026-08-17; the pbook->positions rename lost the original, so dedup could not see it","net":null,"features":null,"fill_source":"confirmed","bucket":"main","origin":null}');
INSERT INTO "pos_log" VALUES(7,'2026-08-19 12:55:40',6,'insert','{"id":6,"symbol":"VCL","cluster":"micro","status":"pending","queued_on":"2026-08-19","entry_day":null,"entry_px":null,"qty":23195,"stop":1.75,"target":2.33,"exit_day":null,"exit_px":null,"exit_reason":null,"net":null,"features":null,"fill_source":null,"bucket":"main","origin":null}');
INSERT INTO "pos_log" VALUES(8,'2026-08-19 13:20:52',6,'update','{"id":6,"symbol":"VCL","cluster":"micro","status":"void","queued_on":"2026-08-19","entry_day":null,"entry_px":null,"qty":23195,"stop":1.75,"target":2.33,"exit_day":"2026-08-19","exit_px":null,"exit_reason":"void: the 2026-08-19 trigger bar was an upper circuit lock (O=H=L=C 1.94, +4.86%, 129 trades, 9 of the last 20 bars locked). The breakout high IS the price band, and at an upper lock there are no sellers, so the next-open fill this order assumes cannot be got. engine.gate() has always rejected high==low but nothing called it; the guard now lives in selection.build and VCL is no longer triggered.","net":null,"features":null,"fill_source":null,"bucket":"main","origin":null}');
CREATE INDEX ix_pos_status ON pos(status);
CREATE TRIGGER pos_no_delete BEFORE DELETE ON pos BEGIN
      SELECT RAISE(ABORT,
        'pos is append-only: a position may be edited, never deleted');
    END;
CREATE TRIGGER pos_log_no_delete BEFORE DELETE ON pos_log BEGIN
      SELECT RAISE(ABORT, 'pos_log is the audit trail and is append-only');
    END;
CREATE VIEW open_orders   AS SELECT * FROM pos WHERE status='open';
CREATE VIEW closed_orders AS SELECT * FROM pos WHERE status='closed';
CREATE UNIQUE INDEX ux_pos_live ON pos(symbol) WHERE status IN ('pending','open');
CREATE VIEW pending_orders AS SELECT * FROM pos WHERE status='pending';
CREATE TRIGGER pos_log_ins AFTER INSERT ON pos BEGIN
      INSERT INTO pos_log(pos_id, action, row)
      VALUES(new.id, 'insert', json_object('id', new.id, 'symbol', new.symbol, 'cluster', new.cluster, 'status', new.status, 'queued_on', new.queued_on, 'entry_day', new.entry_day, 'entry_px', new.entry_px, 'qty', new.qty, 'stop', new.stop, 'target', new.target, 'exit_day', new.exit_day, 'exit_px', new.exit_px, 'exit_reason', new.exit_reason, 'net', new.net, 'features', new.features, 'fill_source', new.fill_source, 'bucket', new.bucket, 'origin', new.origin));
    END;
CREATE TRIGGER pos_log_upd AFTER UPDATE ON pos BEGIN
      INSERT INTO pos_log(pos_id, action, row)
      VALUES(new.id, 'update', json_object('id', new.id, 'symbol', new.symbol, 'cluster', new.cluster, 'status', new.status, 'queued_on', new.queued_on, 'entry_day', new.entry_day, 'entry_px', new.entry_px, 'qty', new.qty, 'stop', new.stop, 'target', new.target, 'exit_day', new.exit_day, 'exit_px', new.exit_px, 'exit_reason', new.exit_reason, 'net', new.net, 'features', new.features, 'fill_source', new.fill_source, 'bucket', new.bucket, 'origin', new.origin));
    END;
COMMIT;
