-- phpMyAdmin SQL Dump
-- version 5.2.1
-- https://www.phpmyadmin.net/
--
-- Host: 127.0.0.1
-- Generation Time: Aug 11, 2025 at 08:31 PM
-- Server version: 10.4.32-MariaDB
-- PHP Version: 8.2.12

SET SQL_MODE = "NO_AUTO_VALUE_ON_ZERO";
START TRANSACTION;
SET time_zone = "+00:00";


/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
/*!40101 SET NAMES utf8mb4 */;

--
-- Database: `project1`
--

-- --------------------------------------------------------

--
-- Table structure for table `tbl_bin`
--

CREATE TABLE `tbl_bin` (
  `category_id` int(11) NOT NULL,
  `id` int(11) NOT NULL,
  `value` int(11) DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

--
-- Dumping data for table `tbl_bin`
--

INSERT INTO `tbl_bin` (`category_id`, `id`, `value`) VALUES
(101, 1, 0),
(102, 2, 0),
(103, 3, 0),
(104, 4, 0),
(105, 5, 0);

-- --------------------------------------------------------

--
-- Table structure for table `tbl_category`
--

CREATE TABLE `tbl_category` (
  `id` int(11) NOT NULL,
  `category_id` int(11) NOT NULL,
  `category_name` varchar(255) NOT NULL,
  `store_id` int(11) DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

--
-- Dumping data for table `tbl_category`
--

INSERT INTO `tbl_category` (`id`, `category_id`, `category_name`, `store_id`) VALUES
(4, 104, 'Paper & Cardboard', 2),
(5, 105, 'Organic Waste', 2),
(8, 1, 'PET', 2),
(9, 2, 'อลูมิเนียม', 2),
(10, 3, 'แก้ว', 2),
(11, 4, 'วัสดุเผา', 2),
(12, 5, 'ขยะปนเปื้อน', 2);

-- --------------------------------------------------------

--
-- Table structure for table `tbl_order`
--

CREATE TABLE `tbl_order` (
  `id` int(11) NOT NULL,
  `order_id` varchar(255) NOT NULL,
  `products_id` varchar(255) DEFAULT NULL,
  `products_name` varchar(255) NOT NULL,
  `quantity` int(11) NOT NULL,
  `disquantity` int(11) NOT NULL DEFAULT 0,
  `email` varchar(255) DEFAULT NULL,
  `order_date` timestamp NOT NULL DEFAULT current_timestamp(),
  `barcode_id` varchar(255) DEFAULT NULL,
  `store_id` int(11) DEFAULT NULL,
  `price` DECIMAL(10,2) DEFAULT NULL -- เพิ่ม price column เข้ามา
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- Table structure for table `tbl_products`
--

CREATE TABLE `tbl_products` (
  `id` int(11) NOT NULL,
  `products_id` varchar(255) NOT NULL,
  `products_name` varchar(255) NOT NULL,
  `price` decimal(10,2) NOT NULL,
  `stock` int(11) NOT NULL,
  `category_id` int(11) DEFAULT NULL,
  `barcode_id` varchar(255) DEFAULT NULL,
  `store_id` int(11) DEFAULT NULL,
  `description` TEXT DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

--
-- Dumping data for table `tbl_products`
--

INSERT INTO `tbl_products` (`id`, `products_id`, `products_name`, `price`, `stock`, `category_id`, `barcode_id`, `store_id`, `description`) VALUES
(1, 'P001', 'Recycled PET Bottle (KG)', 15.00, 95, 1, '1234567890123', 2, 'ขวดพลาสติก PET ใสสำหรับรีไซเคิล'),
(2, 'P002', 'Aluminum Can (KG)', 40.00, 80, 2, '2345678901234', 2, 'กระป๋องอลูมิเนียม'),
(3, 'P003', 'Glass Bottle (KG)', 5.00, 120, 3, '3456789012345', 2, 'ขวดแก้วหลากสี'),
(4, 'P004', 'Newspaper Stack (KG)', 3.00, 150, 4, '4567890123456', 2, 'หนังสือพิมพ์เก่า'),
(5, 'P005', 'Food Waste (KG)', 1.00, 200, 5, '5678901234567', 2, 'เศษอาหาร');

-- --------------------------------------------------------

--
-- Table structure for table `tbl_stores`
--

CREATE TABLE `tbl_stores` (
  `store_id` int(11) NOT NULL,
  `store_name` varchar(255) NOT NULL,
  `location` varchar(255) DEFAULT NULL,
  `contact_email` varchar(255) DEFAULT NULL,
  `moderator_user_id` int(11) DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

--
-- Dumping data for table `tbl_stores`
--

INSERT INTO `tbl_stores` (`store_id`, `store_name`, `location`, `contact_email`, `moderator_user_id`) VALUES
(1, 'Central Recycling Hub', '123 Main St, City', 'info@centralrecycle.com', 2),
(2, 'Green Earth Collection Point', '456 Green Ave, Town', 'contact@greenearth.com', 3);

-- --------------------------------------------------------

--
-- Table structure for table `tbl_users`
--

CREATE TABLE `tbl_users` (
  `id` int(11) NOT NULL,
  `firstname` varchar(255) NOT NULL,
  `lastname` varchar(255) NOT NULL,
  `email` varchar(255) NOT NULL,
  `password` varchar(255) NOT NULL,
  `role` enum('root_admin','administrator','moderator','member','viewer') NOT NULL DEFAULT 'member',
  `store_id` int(11) DEFAULT NULL,
  `is_online` tinyint(1) NOT NULL DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

--
-- Dumping data for table `tbl_users`
--

INSERT INTO `tbl_users` (`id`, `firstname`, `lastname`, `email`, `password`, `role`, `store_id`, `is_online`) VALUES
(1, 'Admin', 'Root', 'pianlerdpringpror@gmail.com', '123456', 'root_admin', NULL, 0),
(2, 'Admin', 'Global', 'admin@example.com', 'adminpass', 'administrator', NULL, 0),
(3, 'Pianlerd', 'Pringpror', 'p@e.com', '1', 'moderator', 2, 1),
(4, 'Member', 'John', 'john@example.com', '1', 'viewer', NULL, 0),
(5, 'Viewer', 'Jane', 'aaa@aaa.com', '1', 'viewer', NULL, 1),
(6, 'p', 'p', 'a@a.com', '1', 'moderator', 2, 0),
(7, 'Member', 'Peter', 'peter@example.com', 'memberpass', 'member', 2, 0),
(8, 'Member', 'Alice', 'alice@example.com', 'memberpass', 'member', NULL, 0);

--
-- Indexes for dumped tables
--

--
-- Indexes for table `tbl_bin`
--
ALTER TABLE `tbl_bin`
  ADD PRIMARY KEY (`id`),
  ADD UNIQUE KEY `category_id` (`category_id`);

--
-- Indexes for table `tbl_category`
--
ALTER TABLE `tbl_category`
  ADD PRIMARY KEY (`id`),
  ADD UNIQUE KEY `category_id` (`category_id`);

--
-- Indexes for table `tbl_order`
--
ALTER TABLE `tbl_order`
  ADD PRIMARY KEY (`id`),
  ADD UNIQUE KEY `order_id` (`order_id`,`products_id`,`email`,`store_id`);

--
-- Indexes for table `tbl_products`
--
ALTER TABLE `tbl_products`
  ADD PRIMARY KEY (`id`),
  ADD UNIQUE KEY `products_id` (`products_id`),
  ADD UNIQUE KEY `barcode_id` (`barcode_id`,`store_id`);

--
-- Indexes for table `tbl_stores`
--
ALTER TABLE `tbl_stores`
  ADD PRIMARY KEY (`store_id`);

--
-- Indexes for table `tbl_users`
--
ALTER TABLE `tbl_users`
  ADD PRIMARY KEY (`id`),
  ADD UNIQUE KEY `email` (`email`);

--
-- AUTO_INCREMENT for dumped tables
--

--
-- AUTO_INCREMENT for table `tbl_category`
--
ALTER TABLE `tbl_category`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT, AUTO_INCREMENT=13;

--
-- AUTO_INCREMENT for table `tbl_order`
--
ALTER TABLE `tbl_order`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT, AUTO_INCREMENT=4;

--
-- AUTO_INCREMENT for table `tbl_products`
--
ALTER TABLE `tbl_products`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT, AUTO_INCREMENT=6;

--
-- AUTO_INCREMENT for table `tbl_stores`
--
ALTER TABLE `tbl_stores`
  MODIFY `store_id` int(11) NOT NULL AUTO_INCREMENT, AUTO_INCREMENT=3;

--
-- AUTO_INCREMENT for table `tbl_users`
--
ALTER TABLE `tbl_users`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT, AUTO_INCREMENT=9;
COMMIT;

/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
