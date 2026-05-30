package com.example.todoapp.data

import androidx.room.*
import kotlinx.coroutines.flow.Flow

@Dao
interface TodoDao {

    @Query("SELECT * FROM todos ORDER BY stars DESC, manualOrder ASC")
    fun getAllTodos(): Flow<List<TodoItem>>

    @Insert
    suspend fun insert(todo: TodoItem): Long

    @Update
    suspend fun update(todo: TodoItem)

    @Delete
    suspend fun delete(todo: TodoItem)

    @Query("UPDATE todos SET manualOrder = :order WHERE id = :id")
    suspend fun updateOrder(id: Long, order: Int)
}
