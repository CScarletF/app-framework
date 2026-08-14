<?php
// Per-table configuration for the generic CrudController.
// Every table you want the API to expose needs an entry here.
//
// Structure:
// 'table_name' => [
//     'columns'        => [...],  // which columns the API is allowed to read/write
//     'required'       => [...],  // which columns MUST be present when creating a row
//     'allowed_values' => [       // mirrors any DB CHECK constraints, so bad
//         'column' => ['val1', 'val2'],   // input gets rejected before it
//     ],                                  // ever reaches Postgres
// ]

return [
    // Example — replace with real tables in app-specific config:
    // 'equipment' => [
    //     'columns' => ['id', 'name', 'category', 'status', 'location',
    //                   'serial_number', 'quantity', 'notes',
    //                   'created_at', 'updated_at'],
    //     'required' => ['name', 'category'],
    //     'allowed_values' => [
    //         'category' => ['server', 'network', 'storage', 'spare_part', 'cable', 'other'],
    //         'status'   => ['in_use', 'spare', 'maintenance', 'retired'],
    //     ],
    // ],
];
