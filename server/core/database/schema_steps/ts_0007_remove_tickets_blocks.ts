import type { SchemaStep } from './contracts.ts';

/** Remove old document-owned Tickets blocks, from the last array element backward. */
export const remove_tickets_blocks_step: SchemaStep = {
  revision: 'ts_0007_remove_tickets_blocks',
  previous_revision: 'ts_0006_answer_receipt_index',
  apply(tx) {
    tx.run(`UPDATE managers SET page = (
        WITH RECURSIVE
        paths AS (
          SELECT child.fullkey AS path, row_number() OVER (ORDER BY child.id DESC) AS n
          FROM json_tree(managers.page) AS child
          JOIN json_tree(managers.page) AS parent ON parent.id = child.parent
          WHERE parent.key = 'blocks' AND parent.type = 'array'
            AND child.type = 'object' AND json_extract(child.value, '$.type') = 'tickets'
        ),
        cleaned(page, n) AS (
          SELECT managers.page, 0
          UNION ALL
          SELECT json_remove(cleaned.page, paths.path), paths.n
          FROM cleaned JOIN paths ON paths.n = cleaned.n + 1
        )
        SELECT page FROM cleaned ORDER BY n DESC LIMIT 1
      ), page_revision = page_revision + 1
      WHERE page IS NOT NULL AND EXISTS (
        SELECT 1 FROM json_tree(managers.page) AS child
        JOIN json_tree(managers.page) AS parent ON parent.id = child.parent
        WHERE parent.key = 'blocks' AND parent.type = 'array'
          AND child.type = 'object' AND json_extract(child.value, '$.type') = 'tickets'
      )`);
    tx.run(`UPDATE chief_page SET page = (
        WITH RECURSIVE
        paths AS (
          SELECT child.fullkey AS path, row_number() OVER (ORDER BY child.id DESC) AS n
          FROM json_tree(chief_page.page) AS child
          JOIN json_tree(chief_page.page) AS parent ON parent.id = child.parent
          WHERE parent.key = 'blocks' AND parent.type = 'array'
            AND child.type = 'object' AND json_extract(child.value, '$.type') = 'tickets'
        ),
        cleaned(page, n) AS (
          SELECT chief_page.page, 0
          UNION ALL
          SELECT json_remove(cleaned.page, paths.path), paths.n
          FROM cleaned JOIN paths ON paths.n = cleaned.n + 1
        )
        SELECT page FROM cleaned ORDER BY n DESC LIMIT 1
      ), page_revision = page_revision + 1
      WHERE page IS NOT NULL AND EXISTS (
        SELECT 1 FROM json_tree(chief_page.page) AS child
        JOIN json_tree(chief_page.page) AS parent ON parent.id = child.parent
        WHERE parent.key = 'blocks' AND parent.type = 'array'
          AND child.type = 'object' AND json_extract(child.value, '$.type') = 'tickets'
      )`);
    tx.run(`UPDATE goals SET page = (
        WITH RECURSIVE
        paths AS (
          SELECT child.fullkey AS path, row_number() OVER (ORDER BY child.id DESC) AS n
          FROM json_tree(goals.page) AS child
          JOIN json_tree(goals.page) AS parent ON parent.id = child.parent
          WHERE parent.key = 'blocks' AND parent.type = 'array'
            AND child.type = 'object' AND json_extract(child.value, '$.type') = 'tickets'
        ),
        cleaned(page, n) AS (
          SELECT goals.page, 0
          UNION ALL
          SELECT json_remove(cleaned.page, paths.path), paths.n
          FROM cleaned JOIN paths ON paths.n = cleaned.n + 1
        )
        SELECT page FROM cleaned ORDER BY n DESC LIMIT 1
      ), revision = revision + 1
      WHERE page IS NOT NULL AND EXISTS (
        SELECT 1 FROM json_tree(goals.page) AS child
        JOIN json_tree(goals.page) AS parent ON parent.id = child.parent
        WHERE parent.key = 'blocks' AND parent.type = 'array'
          AND child.type = 'object' AND json_extract(child.value, '$.type') = 'tickets'
      )`);
  },
};
