<?php

declare(strict_types=1);

$target  = 'https://example.com';
$limit   = 5;
$window  = 60;
$storage = __DIR__ . '/rate_limit.json';

$ip  = $_SERVER['REMOTE_ADDR'] ?? 'unknown';
$now = time();

$data = [];

if (is_file($storage)) {
    $json = file_get_contents($storage);
    $data = json_decode($json, true) ?: [];
}

/*
 * Hapus file jika jumlah IP unik lebih dari 10.
 */
if (count($data) > 10) {
    @unlink($storage);
    $data = [];
}

/*
 * Bersihkan timestamp yang sudah kedaluwarsa.
 */
foreach ($data as $storedIp => $timestamps) {
    $timestamps = array_values(
        array_filter(
            $timestamps,
            fn ($time) => $time > ($now - $window)
        )
    );

    if (empty($timestamps)) {
        unset($data[$storedIp]);
    } else {
        $data[$storedIp] = $timestamps;
    }
}

/*
 * Batasi request per IP.
 */
$requests = $data[$ip] ?? [];

if (count($requests) >= $limit) {
    http_response_code(429);
    header('Retry-After: ' . $window);
    exit('Too Many Requests');
}

$requests[] = $now;
$data[$ip] = $requests;

file_put_contents(
    $storage,
    json_encode($data, JSON_PRETTY_PRINT),
    LOCK_EX
);

header('Location: ' . $target, true, 302);
exit;
