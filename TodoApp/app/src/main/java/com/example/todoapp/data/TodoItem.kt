package com.example.todoapp.data

import androidx.room.Entity
import androidx.room.PrimaryKey

@Entity(tableName = "todos")
data class TodoItem(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val title: String,
    val stars: Int = 0,           // 0–5
    val durationMinutes: Int = 0, // multiples of 5
    val manualOrder: Int = 0,
    val createdAt: Long = System.currentTimeMillis()
)
