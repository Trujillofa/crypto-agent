-- Normalize legacy portfolio rows that encoded market in the symbol suffix.
-- New code persists market in the dedicated column and stores raw symbols.

UPDATE positions
SET
    market = CASE
        WHEN symbol LIKE '%:futures' THEN 'futures'
        WHEN symbol LIKE '%:spot' THEN 'spot'
        ELSE market
    END,
    symbol = REGEXP_REPLACE(symbol, ':(spot|futures)$', '')
WHERE symbol ~ ':(spot|futures)$';

UPDATE trades
SET
    market = CASE
        WHEN symbol LIKE '%:futures' THEN 'futures'
        WHEN symbol LIKE '%:spot' THEN 'spot'
        ELSE market
    END,
    symbol = REGEXP_REPLACE(symbol, ':(spot|futures)$', '')
WHERE symbol ~ ':(spot|futures)$';
